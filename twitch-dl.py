#! /usr/bin/env python3

import os
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import reduce
from itertools import groupby
from optparse import OptionParser, OptionValueError
from sys import stdout, stderr
from threading import Lock
from urllib.parse import urlparse, parse_qs

import m3u8
import requests
import sys
from requests import codes as status


class Chunk:
    def __init__(self, name, size, offset, duration, start):
        self.name = name
        self.size = size
        self.offset = offset
        self.duration = duration
        self.start = start


class ProgressBar:
    def __init__(self, fileName, fileSize):
        self.fileName = fileName
        self.total = fileSize
        self.current = 0
        self.lock = Lock()
        self.updateBy(0)

    def updateBy(self, bytes):
        self.lock.acquire()
        self.current += bytes
        percentCompleted = self.current / self.total * 100
        self.printBar(percentCompleted)
        self.lock.release()

    def printBar(self, percentCompleted):
        info('\r' + ' ' * self.getConsoleWidth())
        info('\r{file} [{percents:3.0f}%]{terminator}'.format(
            file=self.fileName,
            percents=percentCompleted,
            terminator='\n' if self.current == self.total else ''))

    def getConsoleWidth(self):
        _, width = os.popen('stty size', 'r').read().split()
        return int(width)


class CommandLineParser:
    timePattern = '^(((?P<h>0{1,2}|[1-9]\d*):)?((?P<m>[0-5]?[0-9]):))?(?P<s>[0-5]?[0-9])$'

    def __init__(self):
        parser = OptionParser()
        parser.add_option('-s', '--start_time', metavar='START', action='callback', callback=self.toSeconds, type='string', default='0')
        parser.add_option('-e', '--end_time', metavar='END', action='callback', callback=self.toSeconds, type='string', default=str(sys.maxsize))
        parser.usage = '%prog [options] vod_id'
        self.getUsage = lambda: parser.get_usage()
        self.parseArgs = lambda: parser.parse_args()

    def toSeconds(self, option, optString, timeString, parser):
        match = re.search(self.timePattern, timeString)
        if not match:
            raise OptionValueError('Invalid time format for option {}'.format(option.dest))
        ts = dict(map(lambda g: (g, int(match.group(g) or '0')), ['h', 'm', 's']))
        seconds = ts['h'] * 3600 + ts['m'] * 60 + ts['s']
        setattr(parser.values, option.dest, seconds)

    def parseCommandLine(self):
        (options, args) = self.parseArgs()
        if len(args) != 1:
            error(self.getUsage())
        if options.end_time <= options.start_time:
            error("End time can't be earlier than start time\n")
        try:
            return (options.start_time, options.end_time, int(args[0]))
        except ValueError:
            error(self.getUsage())


class Vod:
    def __init__(self, vodId):
        self.vodId = vodId

    def links(self):
        token = self.accessTokenFor(self.vodId)
        recodedToken = {'nauth': token['token'], 'nauthsig': token['sig']}
        res = requests.get('http://usher.justin.tv/vod/{}'.format(self.vodId), params=recodedToken)
        return checkOk(res).content.decode('utf-8')

    def accessTokenFor(self, vodId):
        return self.jsonOf('https://api.twitch.tv/api/vods/{}/access_token'.format(vodId))

    def name(self):
        return self.jsonOf('https://api.twitch.tv/kraken/videos/v{}'.format(self.vodId))['title']

    def jsonOf(self, resource):
        return checkOk(getFrom(resource)).json()

    def sourceQualityLink(self):
        links = self.links().split('\n')
        return next(filter(lambda line: '/high/' in line, links)).replace('/high/', '/chunked/')


progressBar = None


def main():
    global progressBar
    (startTime, endTime, vodId) = CommandLineParser().parseCommandLine()
    vod = Vod(vodId)
    sourceQualityLink = vod.sourceQualityLink()
    (chunks, totalBytes, totalDuration) = withFileOffsets(chunksWithOffsets(contentsOf(sourceQualityLink)))
    baseUrl = sourceQualityLink.rsplit('/', 1)[0]
    fileName = createFile(vod.name() + '.ts')
    progressBar = ProgressBar(fileName, totalBytes)
    downLoadFileFromChunks(fileName, chunks, baseUrl)


def checkOk(response):
    if response.status_code != status.ok:
        error('Failed to get {url}: got {statusCode} response'.format(url=response.url, statusCode=response.status_code))
    return response


def error(msg):
    stderr.write(msg)
    exit(1)


def info(msg):
    stdout.write(msg)
    stdout.flush()


def contentsOf(resource):
    return rawContentsOf(resource).decode('utf-8')


def rawContentsOf(resource):
    return checkOk(getFrom(resource)).content


def getFrom(resource):
    try:
        return requests.get(resource)
    except Exception as e:
        error(str(e))


def chunksWithOffsets(vodLinks):
    playlist = m3u8.loads(vodLinks)
    chunksWithEndOffsets = map(toChunk, playlist.segments)
    return toUberChunks(groupby(chunksWithEndOffsets, lambda c: c[0]))


def toUberChunks(groupedByName):
    uberChunks = OrderedDict()
    for chunkName, chunkGroup in groupedByName:
        chunks = list(chunkGroup)
        uberChunkSize = chunks[-1][1]
        uberChunkDuration = reduce(lambda total, chunk: total + chunk[2], chunks, 0)
        uberChunks[chunkName] = (uberChunkSize, uberChunkDuration)
    return uberChunks


def toChunk(segment):
    parsedLink = urlparse(segment.uri)
    chunkName = parsedLink.path
    endOffset = parse_qs(parsedLink.query)['end_offset'][0]
    return (chunkName, int(endOffset), segment.duration)


def withFileOffsets(chunksWithOffsets):
    fileOffset = 0
    totalDuration = 0
    chunks = []
    for chunkName, (size, duration) in chunksWithOffsets.items():
        chunks.append(Chunk(chunkName, size, fileOffset, duration, totalDuration))
        fileOffset += size + 1
        totalDuration += duration
    return (chunks, fileOffset, totalDuration)


def downLoadFileFromChunks(fileName, chunks, baseUrl):
    with ThreadPoolExecutor(max_workers=10) as executor:
        for chunk in chunks:
            executor.submit(downloadChunkAndWriteToFile, chunk, fileName, baseUrl).add_done_callback(onChunkProcessed)


def createFile(initialName):
    actualName = findSuitable(initialName)
    open(actualName, 'w').close()
    return actualName


def findSuitable(fileName):
    modifier = 0
    newName = fileName
    while os.path.isfile(newName):
        modifier += 1
        newName = re.sub(r'.ts$', ' {:02}.ts'.format(modifier), fileName)
    return fileName if modifier == 0 else newName


def downloadChunkAndWriteToFile(chunk, fileName, baseUrl):
    chunkContents = rawContentsOf('{base}/{chunk}?start_offset=0&end_offset={end}'.format(base=baseUrl, chunk=chunk.name, end=chunk.size))
    return writeContents(chunkContents, fileName, chunk.offset)


def writeContents(chunkContents, fileName, offset):
    with open(fileName, 'rb+') as file:
        file.seek(offset)
        bytesWritten = file.write(chunkContents)
        return bytesWritten


def onChunkProcessed(chunk):
    if chunk.exception():
        error(str(chunk.exception()))
    progressBar.updateBy(chunk.result())


if __name__ == '__main__':
    main()
