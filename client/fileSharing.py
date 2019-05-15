"""This file contains all the code necessary to handle the files-sharing"""

"""@author: Francesco Lorenzo Casciaro - Politecnico di Torino - UPC"""

from random import randint

import peerCore


def sendChunksList(message, thread, localFileList):

    messageFields = message.split()
    key = messageFields[1]
    lastModified = messageFields[2] + " " + messageFields[3]

    if key in localFileList:
        if localFileList[key].lastModified == lastModified:

            answer = str(localFileList[key].availableChunks)

        else:
            answer = "ERROR - DIFFERENT VERSION"
    else:
        answer = "ERROR - UNRECOGNIZED KEY {}".format(key)

    thread.client_sock.send(answer.encode('ascii'))

def sendChunk(message, thread, localFileList):

    messageFields = message.split()
    key = messageFields[1]
    lastModified = messageFields[2] + " " + messageFields[3]
    chunkID = messageFields[4]

    if key in localFileList:
        if localFileList[key].lastModified == lastModified:

            #send chunk
            pass

        else:
            answer = "ERROR - DIFFERENT VERSION"
    else:
        answer = "ERROR - UNRECOGNIZED KEY {}".format(key)

    thread.client_sock.send(answer.encode('ascii'))



def downloadFile(file):

    file.syncLock.acquire()

    if len(file.availableChunks) != 0:
        file.initDownload()

    unavailable = 0

    key = file.groupName + "_" + file.filename

    # ask for the missing peers while the missing list is not empty
    while len(file.missingChunks) > 0 and unavailable < 5:

        """retrieve the list of active peers for the file"""
        activePeers = peerCore.retrievePeers(file.groupName, selectAll=False)

        """chunks_peers is a dictionary where key is the chunkID and
        value is the list of peer which have that chunk"""
        chunks_peers = dict()
        """chunksCounter is a dictionary where key is the chunkID and
        value is the number of peers having that chunk"""
        chunksCounter = dict()

        for peer in activePeers:
            """ask each peer which chunks it has and collect informations
            in order to apply the rarest-first approach"""

            #print(peer)

            chunksList = getChunksList(key, file.lastModified, peer["peerIP"], peer["peerPort"])

            if chunksList is not None:

                for chunk in chunksList:
                    if chunk in chunksCounter:
                        chunksCounter[chunk] += 1
                        chunks_peers[chunk].append(peer)
                    else:
                        chunksCounter[chunk] = 1
                        chunks_peers[chunk] = list()
                        chunks_peers[chunk].append(peer)

        """ask for the 10 rarest chunks"""
        i = 0
        askFor = 10

        for chunk in sorted(chunksCounter, key=chunksCounter.get):

            if len(file.missingChunks) == 0:
                break

            if chunksCounter[chunk] == 0 :
                """it's impossible to download the file because the seed is unactive 
                and the other peers don't have chunks yet"""
                unavailable += 1
                break

            if chunk in file.missingChunks:

                """choose a random peer from the list"""
                r = randint(0, len(chunks_peers[chunk]))
                peerIP = chunks_peers[chunk][r]["peerIP"]
                peerPort = chunks_peers[chunk][r]["peerPort"]

                if getChunk(file, chunk, peerIP, peerPort):
                    i += 1
                    if i >= askFor:
                        break

        del chunksCounter
        del chunks_peers

    if unavailable == 4:
        file.syncLock.release()
        return

    file.status = "S"
    file.syncLock.release()

def getChunksList(key, lastModified, peerIP, peerPort):

    chunksList = list()

    s = peerCore.createSocket(peerIP, peerPort)

    message = "CHUNKS_LIST {} {}".format(key, lastModified)
    s.send(message.encode('ascii'))

    data = s.recv(peerCore.BUFSIZE)
    print('Received from the peer :', str(data.decode('ascii')))

    peerCore.closeSocket(s)

    if str(data.decode('ascii')).split()[0] == "ERROR":
        #return empty list
        return chunksList
    else:
        chunksList = eval(str(data.decode('ascii')))
        return chunksList



def getChunk(file, chunkID, peerIP, peerPort):

    file.missingChunks.remove(chunkID)
    file.availableChunks.append(chunkID)


