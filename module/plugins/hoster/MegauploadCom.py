#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re

from module.plugins.Hoster import Hoster

from module.network.RequestFactory import getURL

from module.unescape import unescape
from module.PyFile import statusMap

from pycurl import error

def getInfo(urls):

    result = []
    
    # MU API request 
    post = {}
    fileIds = [x.split("=")[-1] for x in urls]  # Get ids from urls
    for i, fileId in enumerate(fileIds):
        post["id%i" % i] = fileId
    response = getURL(MegauploadCom.API_URL, post=post)
    
    # Process API response
    parts = [re.split(r"&(?!amp;|#\d+;)", x) for x in re.split(r"&?(?=id[\d]+=)", response)]
    apiHosterMap = dict([elem.split('=') for elem in parts[0]])
    for entry in parts[1:]:
        apiFileDataMap = dict([elem.split('=') for elem in entry])
        apiFileId = [key for key in apiFileDataMap.keys() if key.startswith('id')][0]
        i = int(apiFileId.replace('id', ''))
            
        # File info
        fileInfo = _translateAPIFileInfo(apiFileId, apiFileDataMap, apiHosterMap)
        url = urls[i]
        name = fileInfo.get('name', url)
        size = fileInfo.get('size', 0)
        status = fileInfo.get('status', statusMap['queued'])
        
        # Add result
        result.append( (name, size, status, url ) )
    
    yield result
    
def _translateAPIFileInfo(apiFileId, apiFileDataMap, apiHosterMap):
    
    # Translate
    fileInfo = {}
    try:
        fileInfo['status'] = MegauploadCom.API_STATUS_MAPPING[apiFileDataMap[apiFileId]]
        fileInfo['name'] = apiFileDataMap['n'] 
        fileInfo['size'] = int(apiFileDataMap['s'])
        fileInfo['hoster'] = apiHosterMap[apiFileDataMap['d']]        
    except:
        pass

    return fileInfo

class MegauploadCom(Hoster):
    __name__ = "MegauploadCom"
    __type__ = "hoster"
    __pattern__ = r"http://[\w\.]*?(megaupload)\.com/.*?(\?|&)d=[0-9A-Za-z]+"
    __version__ = "0.23"
    __description__ = """Megaupload.com Download Hoster"""
    __author_name__ = ("spoob")
    __author_mail__ = ("spoob@pyload.org")
    
    API_URL = "http://megaupload.com/mgr_linkcheck.php"
    API_STATUS_MAPPING = {"0": statusMap['online'], "1": statusMap['offline'], "3": statusMap['temp. offline']} 

    def init(self):
        self.html = [None, None]
        if self.account:
            self.premium = self.account.getAccountInfo(self.user)["premium"]

        if not self.premium:
            self.multiDL = False
            self.chunkLimit = 1

        self.api = {}
        none, sep, self.fileID = self.pyfile.url.partition("d=")
        self.pyfile.url = "http://www.megaupload.com/?d=" + self.fileID

        
    def process(self, pyfile):
        if not self.account or not self.premium:
            self.download_html()
            self.download_api()

            if not self.file_exists():
                self.offline()

            time = self.get_wait_time()
            self.setWait(time)
            self.wait()
            
            pyfile.name = self.get_file_name()
            self.download(self.get_file_url())

            check = self.checkDownload({"limit": "Download limit exceeded"})
            if check == "limit":
                wait = self.load("http://www.megaupload.com/?c=premium&l=1")
                try:
                    wait = re.search(r"Please wait (\d+) minutes", wait).group(1)
                except:
                    wait = 1
                self.log.info(_("Megaupload: waiting %d minutes") % int(wait))
                self.setWait(int(wait)*60, True)
                self.wait()
                if not self.premium:
                    self.req.clearCookies()
                self.process(pyfile)
        else:
            self.download_api()
            pyfile.name = self.get_file_name()

            try:
                self.download(pyfile.url)
            except error, e:
                if e.args and e.args[0] == 33:
                    # undirect download and resume , not a good idea
                    page = self.load(pyfile.url)
                    self.download(re.search(r'href=\"(http://[^\"]*?)\" class=\"down_ad_butt1\">', page).group(1))
                    return 
                else:
                    raise

            check = self.checkDownload({"dllink": re.compile(r'href=\"(http://[^\"]*?)\" class=\"down_ad_butt1\">')})
            if check == "dllink":
                self.log.warning(_("You should enable direct Download in your Megaupload Account settings"))

                pyfile.size = 0
                self.download(self.lastCheck.group(1))

    def download_html(self):        
        for i in range(3):
            self.html[0] = self.load(self.pyfile.url)
            self.html[1] = self.html[0] # in case of no captcha, this already contains waiting time, etc
            count = 0
            if "The file that you're trying to download is larger than 1 GB" in self.html[0]:
                self.fail(_("You need premium to download files larger than 1 GB"))
                
            if r'Please enter the password below' in self.html[0]:
                pw = self.getPassword()
                if not pw:
                    self.fail(_("The file is password protected, enter a password and restart."))

                self.html[1] = self.load(self.pyfile.url, post={"filepassword":pw})
                break # looks like there is no captcha for pw protected files

            while "document.location='http://www.megaupload.com/?c=msg" in self.html[0]:
                # megaupload.com/?c=msg usually says: Please check back in 2 minutes,
                # so we can spare that http request
                self.setWait(120)
                if count > 1:
                    self.wantReconnect = True

                self.wait()
                
                self.html[0] = self.load(self.pyfile.url)
                count += 1
                if count > 5:
                    self.fail(_("Megaupload is currently blocking your IP. Try again later, manually."))
            
            try:
                url_captcha_html = re.search('(http://[\w\.]*?megaupload\.com/gencap.php\?.*\.gif)', self.html[0]).group(1)
            except:
                continue

            captcha = self.decryptCaptcha(url_captcha_html)
            captchacode = re.search('name="captchacode" value="(.*)"', self.html[0]).group(1)
            megavar = re.search('name="megavar" value="(.*)">', self.html[0]).group(1)
            self.html[1] = self.load(self.pyfile.url, post={"captcha": captcha, "captchacode": captchacode, "megavar": megavar})
            if re.search(r"Waiting time before each download begins", self.html[1]) is not None:
                break

    def download_api(self):

        # MU API request 
        fileId = self.pyfile.url.split("=")[-1] # Get file id from url
        apiFileId = "id0"
        post = {apiFileId: fileId}
        response = getURL(self.API_URL, post=post)    
        self.log.debug("%s: API response [%s]" % (self.__name__, response))
        
        # Translate API response
        parts = [re.split(r"&(?!amp;|#\d+;)", x) for x in re.split(r"&?(?=id[\d]+=)", response)]
        apiHosterMap = dict([elem.split('=') for elem in parts[0]])
        apiFileDataMap = dict([elem.split('=') for elem in parts[1]])        
        self.api = _translateAPIFileInfo(apiFileId, apiFileDataMap, apiHosterMap)

        # File info
        try:
            self.pyfile.status = self.api['status']
            self.pyfile.name = self.api['name'] 
            self.pyfile.size = self.api['size']
        except KeyError:
            self.log.warn("%s: Cannot recover all file [%s] info from API response." % (self.__name__, fileId))
        
        # Fail if offline
        if self.pyfile.status == statusMap['offline']:
            self.offline()

    def get_file_url(self):
        file_url_pattern = 'id="downloadlink"><a href="(.*)"\s+(?:onclick|class)="'
        search = re.search(file_url_pattern, self.html[1])
        return search.group(1).replace(" ", "%20")

    def get_file_name(self):
        try:
            return self.api["name"]
        except KeyError:
            file_name_pattern = 'id="downloadlink"><a href="(.*)" onclick="'
            return re.search(file_name_pattern, self.html[1]).group(1).split("/")[-1]

    def get_wait_time(self):
        time = re.search(r"count=(\d+);", self.html[1])
        if time:
            return time.group(1)
        else:
            return 45

    def file_exists(self):
        #self.download_html()
        if re.search(r"Unfortunately, the link you have clicked is not available.", self.html[0]) is not None or \
            re.search(r"Download limit exceeded", self.html[0]) is not None:
            return False
            
        if re.search("The file you are trying to access is temporarily unavailable", self.html[0]) is not None:
            self.setWait(120)
            self.log.debug("%s: The file is temporarily not available. Waiting 2 minutes." % self.__name__)
            self.wait()
            
            self.download_html()
            if re.search("The file you are trying to access is temporarily unavailable", self.html[0]) is not None:
                self.fail(_("Looks like the file is still not available. Retry downloading later, manually."))
            
        if re.search("The password you have entered is not correct", self.html[1]):
            self.fail(_("Wrong password for download link."))
            
        return True
