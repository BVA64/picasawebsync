#!/usr/bin/python

from gdata.photos.service import  *
import gdata.media
import gdata.geo
import os
import re
import pprint
import sys
import argparse
import mimetypes
import hashlib
import urllib
import time
import datetime
import urllib
import json
import time

# Upload video code came form http://nathanvangheem.com/news/moving-to-picasa-update
class VideoEntry(gdata.photos.PhotoEntry):
    pass
    
gdata.photos.VideoEntry = VideoEntry

def InsertVideo(self, album_or_uri, video, filename_or_handle, content_type='image/jpeg'):
    """Copy of InsertPhoto which removes protections since it *should* work"""
    try:
        assert(isinstance(video, VideoEntry))
    except AssertionError:
        raise GooglePhotosException({'status':GPHOTOS_INVALID_ARGUMENT,
            'body':'`video` must be a gdata.photos.VideoEntry instance',
            'reason':'Found %s, not PhotoEntry' % type(video)
        })
    try:
        majtype, mintype = content_type.split('/')
        #assert(mintype in SUPPORTED_UPLOAD_TYPES)
    except (ValueError, AssertionError):
        raise GooglePhotosException({'status':GPHOTOS_INVALID_CONTENT_TYPE,
            'body':'This is not a valid content type: %s' % content_type,
            'reason':'Accepted content types:'
        })
    if isinstance(filename_or_handle, (str, unicode)) and \
        os.path.exists(filename_or_handle): # it's a file name
        mediasource = gdata.MediaSource()
        mediasource.setFile(filename_or_handle, content_type)
    elif hasattr(filename_or_handle, 'read'):# it's a file-like resource
        if hasattr(filename_or_handle, 'seek'):
            filename_or_handle.seek(0) # rewind pointer to the start of the file
        # gdata.MediaSource needs the content length, so read the whole image 
        file_handle = StringIO.StringIO(filename_or_handle.read()) 
        name = 'image'
        if hasattr(filename_or_handle, 'name'):
            name = filename_or_handle.name
        mediasource = gdata.MediaSource(file_handle, content_type,
            content_length=file_handle.len, file_name=name)
    else: #filename_or_handle is not valid
        raise GooglePhotosException({'status':GPHOTOS_INVALID_ARGUMENT,
            'body':'`filename_or_handle` must be a path name or a file-like object',
            'reason':'Found %s, not path name or object with a .read() method' % \
            type(filename_or_handle)
        })

    if isinstance(album_or_uri, (str, unicode)): # it's a uri
        feed_uri = album_or_uri
    elif hasattr(album_or_uri, 'GetFeedLink'): # it's a AlbumFeed object
        feed_uri = album_or_uri.GetFeedLink().href

    try:
        return self.Post(video, uri=feed_uri, media_source=mediasource,
            converter=None)
    except gdata.service.RequestError, e:
        raise GooglePhotosException(e.args[0])
        
gdata.photos.service.PhotosService.InsertVideo = InsertVideo

# Class to store details of an album
class Albums:
    def __init__(self, rootDirs, albumNaming):
        self.rootDirs = rootDirs
        self.albums = self.scanFileSystem(albumNaming)
    # walk the directory tree populating the list of files we have locally
    def scanFileSystem(self, albumNaming):
        fileAlbums = {}
        for rootDir in self.rootDirs:
            for dirName,subdirList,fileList in os.walk( rootDir ) :
                albumName = convertDirToAlbum(albumNaming, rootDir,  dirName)
                # have we already seen this album? If so append our path to it's list
                if albumName in fileAlbums:
                    album = fileAlbums[albumName]
                    thisRoot = album.suggestNewRoot(dirName)
                else:
                    # create a new album
                    thisRoot = dirName
                    album = AlbumEntry(dirName,  albumName)
                    fileAlbums[albumName] = album
                # now iterate it's files to add them to our list
                for fname in fileList :
                    fullFilename = os.path.join(dirName, fname)
                    # figure out the filename relative to the root dir of the album (to ensure uniqeness) 
                    relFileName = re.sub("^/","", fullFilename[len(thisRoot):])
                    fileEntry = FileEntry(relFileName, fullFilename,  None, True, album)
                    album.entries[relFileName] = fileEntry
        if verbose:
            print ("Found "+str(len(fileAlbums))+" albums on the filesystem")
        return fileAlbums;
    def scanWebAlbums(self, deletedups):
        # walk the web album finding albums there
        webAlbums = gd_client.GetUserFeed()
        for webAlbum in webAlbums.entry:
            webAlbumTitle = Albums.flatten(webAlbum.title.text)
            # print "Album %s is %s in %s" % (webAlbumTitle, webAlbumTitle in self.albums,  ",".join(self.albums))
            if webAlbumTitle in self.albums:
                foundAlbum = self.albums[webAlbumTitle]
                self.scanWebPhotos(foundAlbum, webAlbum,  deletedups)
            else:
                album = AlbumEntry(os.path.join(self.rootDirs[0], webAlbum.title.text),  webAlbum.title.text)
                self.albums[webAlbum.title.text] = album
                self.scanWebPhotos(album, webAlbum,  deletedups)
            if verbose:
                print ('Scanned web-album %s (containing %s files)' % (webAlbum.title.text, webAlbum.numphotos.text))
    def scanWebPhotos(self, foundAlbum, webAlbum,  deletedups):
        photos = repeat(lambda: gd_client.GetFeed(webAlbum.GetPhotosUri()), "list photos in album %s" % foundAlbum.albumName, True)
        webAlbum = WebAlbum(webAlbum, int(photos.total_results.text))
        foundAlbum.webAlbum.append(webAlbum)
        for photo in photos.entry:
            photoTitle=urllib.unquote(photo.title.text)
            if photoTitle in foundAlbum.entries:
                entry = foundAlbum.entries[photoTitle]
                if entry.isWeb():
                    if(deletedups):
                        print "Deleted dupe of %s on server" % photoTitle
                        repeat(lambda: gd_client.Delete(photo), "deleting dupe %s" % photoTitle, False)
                    else:
                        print "WARNING: More than one copy of %s - ignoring" % photoTitle
                else:
                    entry.setWebReference(photo)
                # or photo.exif.time
            else:
                fileEntry = FileEntry(photoTitle, None,  photo, False, foundAlbum)
                foundAlbum.entries[photoTitle] = fileEntry
    def uploadMissingAlbumsAndFiles(self, compareattributes, mode, test):
        size = 0
        for album in self.albums.itervalues():
           size+= len(album.entries)
        count = 0
        actionCounts = {}
        for action in Actions:
            actionCounts[action]=0
        for album in self.albums.itervalues():
            for file in album.entries.itervalues():
                changed = file.changed(compareattributes)
                if verbose:
                    print ("%s (%s) #%s/%s - %s" % (mode[changed],changed, str(count),str(size),file.getFullName()))
                if not test:
                    repeat(lambda: getattr(file, mode[changed].lower())(changed), "%s on %s identified as %s" % (mode[changed],  file.getFullName(), changed ), False)
                actionCounts[mode[changed]]+=1
                count += 1
        print("Finished transferring files. Total files found %s, composed of %s" % (count, str(actionCounts)))
    @staticmethod 
    def createAlbumName(name,  index):
        if index == 0:
            return name
        else:
            return "%s #%s" % (name, index)
    @staticmethod
    def flatten(name):
        return re.sub("#[0-9]*$","",name).rstrip()
        

class AlbumEntry:
    def __init__(self, fileName,  albumName):
        self.paths = [fileName]
        self.rootPath= fileName
        self.albumName = albumName
        self.entries = {}
        self.webAlbum = []
        self.webAlbumIndex = 0
    def __str__(self):
        return (self.getAlbumName()+" under "+self.rootPath+" "+str(len(self.entries))+" entries "+\
            ["exists","doesn't exist"][not self.webAlbum]+" online")
    def getAlbumName(self):
        return self.albumName
    def getPathsAsString(self):
        return ",".join(self.paths)
    def suggestNewRoot(self, name):
        for path in self.paths:
            if name.startswith(path):
                return path
        self.paths.append(name)
        return name
    
# Class to store web album details

class WebAlbum:
    def __init__(self, album,  numberFiles):
        self.albumUri = album.GetPhotosUri()
        self.albumTitle = album.title.text
        self.numberFiles = numberFiles


# Class to store details of an individual file

class FileEntry:
    def __init__(self, name, path,  webReference,  isLocal,  album):
        self.name = name
        if path:
            self.path=path
        else:
            self.path=os.path.join(album.rootPath, name)
        self.isLocal=isLocal
        self.localHash=None
        self.remoteHash=None
        self.remoteDate=None
        self.remoteSize=None
        self.album=album
        self.setWebReference(webReference)
    def setWebReference(self, webReference):
        if webReference:
            self.gphoto_id = webReference.gphoto_id.text
            self.albumid = webReference.albumid.text
            self.webUrl = webReference.content.src
            self.remoteHash = webReference.checksum.text
            self.remoteDate = time.mktime(time.strptime( re.sub("\.[0-9]{3}Z$",".000 UTC",webReference.updated.text),'%Y-%m-%dT%H:%M:%S.000 %Z'))
            self.remoteSize = int(webReference.size.text)
        else:
            self.webUrl = None
    def getEditObject(self):
        if self.gphoto_id:
            photo = gd_client.GetFeed('/data/feed/api/user/%s/albumid/%s/photoid/%s' % ("default", self.albumid,  self.gphoto_id))
            return photo
        # FIXME throw exception
        return None
    def getFullName(self):
        return self.album.getAlbumName()+" "+self.name
    def getLocalHash(self):
        if not(self.localHash):
            md5 = hashlib.md5()
            with open(self.path,'rb') as f: 
                for chunk in iter(lambda: f.read(128*md5.block_size), b''): 
                     md5.update(chunk)
            self.localHash = md5.hexdigest()
        return self.localHash
    def getLocalDate(self):
        return os.path.getmtime(self.path)
    def getLocalSize(self):
        return os.path.getsize(self.path)
    def changed(self, compareattributes):
        if self.isLocal:
            if self.isWeb():
            # filesize (2), date (1),  hash (4) 
                if compareattributes & 1:
                    if self.remoteDate < self.getLocalDate() + 60:
                        # print "%s: remote=%s and local=%s" % (self.getFullName(), time.gmtime(self.remoteDate), time.gmtime(self.getLocalDate()))
                        return Comparisons.REMOTE_OLDER     
                if compareattributes & 2: 
                    if self.remoteSize != self.getLocalSize():
                        return Comparisons.DIFFERENT        
                if compareattributes & 4:                
                    if self.remoteHash:
                        if self.remoteHash != self.getLocalHash():
                            return Comparisons.DIFFERENT
                    else:
                        return Comparisons.UNKNOWN
                return Comparisons.SAME
            else:
                return Comparisons.LOCAL_ONLY
        else:
            return Comparisons.REMOTE_ONLY
    def isWeb(self):
        return self.webUrl != None
    # UPLOAD_LOCAL', 'DELETE_LOCAL', 'SILENT', 'REPORT', 'DOWNLOAD_REMOTE', 'DELETE_REMOTE', 'TAG_REMOTE', 'REPLACE_REMOTE_WITH_LOCAL', 'UPDATE_REMOTE_METADATA'
    def delete_local(self, event):
        print ("Not implemented delete")
    def silent(self, event):
        None
    def report(self, event):
        print ("Identified %s as %s - taking no action" % (self.name, event))
    def tag_remote(self, event):
        print ("Not implemented tag")
    def replace_remote_with_local(self, event):
        self.delete_remote(event)
        self.upload_local(event)
    def update_remote_metadata(self, event):
        entry = gd_client.GetEntry(self.getEditObject().GetEditLink().href)
        self.addMetadata(entry)
        self.setWebReference(gd_client.UpdatePhotoMetadata(entry))
    def download_remote(self, event):
        url = self.webUrl
        path = os.path.split(self.path)[0]
        if not os.path.exists(path):
            os.makedirs(path)
        urllib.urlretrieve(url, self.path)
        os.utime(path, (int(self.remoteDate), int(self.remoteDate)))
    def delete_remote(self, event):
        gd_client.Delete(self.getEditObject())        
    def upload_local(self, event):
        mimeType = mimetypes.guess_type(self.path)[0]
        if mimeType in chosenFormats:
            while (self.album.webAlbumIndex<len(self.album.webAlbum) and self.album.webAlbum[self.album.webAlbumIndex].numberFiles >= 999):
                self.album.webAlbumIndex = self.album.webAlbumIndex + 1                        
            if self.album.webAlbumIndex>=len(self.album.webAlbum):
                subAlbum = WebAlbum(gd_client.InsertAlbum(title=Albums.createAlbumName(self.album.getAlbumName(), self.album.webAlbumIndex), access='private', summary='synced from '+self.album.rootPath), 0)
                self.album.webAlbum.append(subAlbum)
                if verbose:
                    print ('Created album %s to sync %s' % (subAlbum.albumTitle, self.album.rootPath))
            else:
                subAlbum = self.album.webAlbum[self.album.webAlbumIndex]
            if mimeType in supportedImageFormats:
                photo = self.upload_local_img(subAlbum, mimeType)   
            if mimeType in supportedVideoFormats:            
                photo = self.upload_local_video(subAlbum, mimeType) 
        else:
            print ("Skipped %s (because can't upload file of type %s)." % (self.path, mimeType))
    def upload_local_img(self,  subAlbum, mimeType):
            name = urllib.quote(self.name, '')
            metadata = gdata.photos.PhotoEntry()
            metadata.title=atom.Title(text=name) # have to quote as certain charecters, e.g. / seem to break it
            self.addMetadata(metadata)
            photo = gd_client.InsertPhoto(subAlbum.albumUri, metadata, self.path, mimeType) 
            subAlbum.numberFiles = subAlbum.numberFiles + 1
            return photo 
    def upload_local_video(self,  subAlbum, mimeType):
            name = urllib.quote(self.name, '')
            metadata = gdata.photos.VideoEntry()
            metadata.title=atom.Title(text=name) # have to quote as certain charecters, e.g. / seem to break it
            self.addMetadata(metadata)
            photo = gd_client.InsertVideo(subAlbum.albumUri, metadata, self.path, mimeType) 
            subAlbum.numberFiles = subAlbum.numberFiles + 1
            return photo
    def addMetadata(self, metadata):
            metadata.summary = atom.Summary(text='synced from '+self.path, summary_type='text')
            metadata.checksum= gdata.photos.Checksum(text=self.getLocalHash())
    
# Method to translate directory name to an album name   
    
def convertDirToAlbum(formElements,  root,  name):
    if root == name:
        return "Home"
    nameElements = re.split("/", re.sub("^/","",name[len(root):]))
    which = min(len(formElements), len(nameElements))
    work = formElements[which-1].format(*nameElements)
    return work

supportedImageFormats = frozenset(["image/bmp", "image/gif",  "image/jpeg",  "image/png"])
supportedVideoFormats = frozenset(["video/3gpp", "video/avi", "video/quicktime", "video/mp4", "video/mpeg", "video/mpeg4", "video/msvideo", "video/x-ms-asf", "video/x-ms-wmv", "video/x-msvideo"])


class Enum(set):
    def __getattr__(self, name):
        if name in self:
            return name
        raise AttributeError
    
Comparisons = Enum(['REMOTE_OLDER', 'DIFFERENT', 'SAME', 'UNKNOWN', 'LOCAL_ONLY', 'REMOTE_ONLY'])   
Actions = Enum(['UPLOAD_LOCAL', 'DELETE_LOCAL', 'SILENT', 'REPORT', 'DOWNLOAD_REMOTE', 'DELETE_REMOTE', 'TAG_REMOTE', 'REPLACE_REMOTE_WITH_LOCAL', 'UPDATE_REMOTE_METADATA'])
UploadOnlyActions = {
        Comparisons.REMOTE_OLDER:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.DIFFERENT:Actions.REPORT, 
        Comparisons.SAME:Actions.SILENT, 
        Comparisons.UNKNOWN:Actions.REPORT, 
        Comparisons.LOCAL_ONLY:Actions.UPLOAD_LOCAL, 
        Comparisons.REMOTE_ONLY:Actions.REPORT}
DownloadOnlyActions = {
        Comparisons.REMOTE_OLDER:Actions.REPORT, 
        Comparisons.DIFFERENT:Actions.DOWNLOAD_REMOTE, 
        Comparisons.SAME:Actions.SILENT, 
        Comparisons.UNKNOWN:Actions.REPORT, 
        Comparisons.LOCAL_ONLY:Actions.REPORT, 
        Comparisons.REMOTE_ONLY:Actions.DOWNLOAD_REMOTE}
PassiveActions = {
        Comparisons.REMOTE_OLDER:Actions.REPORT, 
        Comparisons.DIFFERENT:Actions.REPORT, 
        Comparisons.SAME:Actions.SILENT, 
        Comparisons.UNKNOWN:Actions.REPORT, 
        Comparisons.LOCAL_ONLY:Actions.REPORT, 
        Comparisons.REMOTE_ONLY:Actions.REPORT}        
RepairActions= {
        Comparisons.REMOTE_OLDER:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.DIFFERENT:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.SAME:Actions.SILENT,  
        Comparisons.UNKNOWN:Actions.UPDATE_REMOTE_METADATA, 
        Comparisons.LOCAL_ONLY:Actions.UPLOAD_LOCAL, 
        Comparisons.REMOTE_ONLY:Actions.DELETE_REMOTE}
SyncActions= {
        Comparisons.REMOTE_OLDER:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.DIFFERENT:Actions.REPORT, 
        Comparisons.SAME:Actions.SILENT,  
        Comparisons.UNKNOWN:Actions.REPORT, 
        Comparisons.LOCAL_ONLY:Actions.UPLOAD_LOCAL, 
        Comparisons.REMOTE_ONLY:Actions.DOWNLOAD_REMOTE}
SyncUploadActions= {
        Comparisons.REMOTE_OLDER:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.DIFFERENT:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.SAME:Actions.SILENT,  
        Comparisons.UNKNOWN:Actions.REPLACE_REMOTE_WITH_LOCAL, 
        Comparisons.LOCAL_ONLY:Actions.UPLOAD_LOCAL, 
        Comparisons.REMOTE_ONLY:Actions.DELETE_REMOTE}

modes = {'upload':UploadOnlyActions, 'download':PassiveActions, 'report':PassiveActions, 'repairUpload':RepairActions,'sync':SyncActions, 'syncUpload':SyncUploadActions}
formats = {'photo': supportedImageFormats,  'video':supportedVideoFormats,  'both':supportedImageFormats.union(supportedVideoFormats)}


def convertMode(string):
    return modes[string]
    
def convertFormat(string):
    return formats[string]

def repeat(function,  description, onFailRethrow):
    exc_info = None
    for attempt in range(3):
        try:
            if verbose and (attempt > 0):
                print ("Trying %s attempt %s" % (description, attempt) )    
            return function()
        except Exception,  e:
            exc_info = e
            # FIXME - to try and stop 403 token expired
            time.sleep(6)
            gd_client.ProgrammaticLogin()
            continue
        else:
            break
    else:
        print ("WARNING: Failed to %s. This was due to %s" % (description, exc_info))
        if onFailRethrow:
            raise exc_info

# start of the program

defaultNamingFormat=["{0}", "{1} ({0})"]

parser = argparse.ArgumentParser()
parser.add_argument("-u","--username", help="Your picassaweb username")
parser.add_argument("-p","--password", help="Your picassaweb password")
parser.add_argument("-d","--directory",  nargs='+',help="The local directories. The first of these will be used for any downloaded items")
parser.add_argument("-n","--naming", default=defaultNamingFormat,  nargs='+',help="Expression to convert directory names to web album names. Formed as a ~ seperated list of substitution strings, "
"so if a sub directory is in the root scanning directory then the first slement will be used, if there is a directory between them the second, etc. If the directory path is longer than the "
"list then the last element is used (and thus the path is flattened). Default is \"%s\"" % defaultNamingFormat)
# parser.add_argument("-m", "--metadatalevel", type=convertImpactLevel, help="metadata level %s" % list(activityLevels),  default="upload")
parser.add_argument("-c", "--compareattributes", type=int, help="set of flags to indicate whether to use date (1), filesize (2), hash (4) in addition to filename. "
"These are applied in order from left to right with a difference returning immediately and a similarity passing on to the next check."
"They work like chmod values, so add the values in brackets to switch on a check. Date uses a 60 second margin (to allow for different time stamp"
"between google and your local machine, and can only identify a locally modified file not a remotely modified one. Filesize and hash are used by default",  default=5)
parser.add_argument("-v","--verbose", default=False,  action='store_true',  help="Increase verbosity")
parser.add_argument("-t","--test", default=False,  action='store_true',  help="Don't actually run activities, but report what you would have done (you may want to enable verbose)")
parser.add_argument("-m","--mode", type=convertMode, help="The mode is a preset set of actions to execute in different circumstances, e.g. upload, download, sync, etc. The full set of optoins is %s. "
"The default is upload. Look at the github page for full details of what each action does" % list(modes),  default="upload")
parser.add_argument("-dd","--deletedups", default=False,  action='store_true',  help="Delete any remote side duplicates")
parser.add_argument("-f","--format", type=convertFormat,  default="photo",  help="Upload photos, videos or both")
for comparison in Comparisons:
    parser.add_argument("--override:%s"%comparison, default=None,  help="Override the action for %s from the list of %s" % (comparison, ",".join(list(Actions))))
args = parser.parse_args()


chosenFormats = args.format

gd_client = gdata.photos.service.PhotosService()
gd_client.email = args.username # Set your Picasaweb e-mail address...
gd_client.password = args.password 
gd_client.source = 'api-sample-google-com'
gd_client.ProgrammaticLogin()
verbose=args.verbose

rootDirs = args.directory # set the directory you want to start from

albumNaming = args.naming
mode = args.mode
for comparison in Comparisons:
    r = getattr(args, "override:%s"%comparison, None)
    if r:
        mode[comparison]=r

albums = Albums(rootDirs, albumNaming)
albums.scanWebAlbums(args.deletedups)
albums.uploadMissingAlbumsAndFiles(args.compareattributes, mode, args.test)

