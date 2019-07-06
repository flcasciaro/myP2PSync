import os
import time
from collections import deque
from threading import Thread, Lock

import fileManagement
import fileSharing
import peerCore

# Data structure where sync operations will be scheduled
queue = deque()
queueLock = Lock()

# Global variable used to stop the sync scheduler thread
stop = False

# Data structure that keep tracks of synchronization threads
# key : groupName_filename
# values: dict()    ->      groupName
#                           stop
syncThreads = dict()
syncThreadsLock = Lock()

# Maximum number of synchronization threads working at the same time
MAX_SYNC_THREAD = 5

# define synchronization thread possible states
# synchronization thread is working
SYNC_RUNNING = 0
# synchronization successfully completed
SYNC_SUCCESS = 1
# synchronization failed
SYNC_FAILED = 2
# synchronization stopped by group disconnection
SYNC_STOPPED = 3
# synchronization stopped by file removal action
FILE_REMOVED = 4
# synchronization stopped by file update action
FILE_UPDATED = 5
# undefined state
UNDEFINED_STATE = 6


class syncTask:

    def __init__(self, groupName, fileTreePath, timestamp):

        self.groupName = groupName
        self.fileTreePath = fileTreePath
        self.timestamp = timestamp

    def toString(self):

        return "SYNC {} {} {}".format(self.groupName, self.fileTreePath, self.timestamp)

    def isOutdated(self, newTask):
        """
        Check if a syncTask is outdated.
        A task is outdated if it refers to the same file of a new task but the timestamp is older (or equal).
        :param self: checked task
        :param newTask: new task
        :return: boolean (True if the task is outdated, otherwise False)
        """

        if self.groupName == newTask.groupName and self.fileTreePath == newTask.fileTreePath:
            if self.timestamp <= newTask.timestamp:
                return True

        return False


def scheduler():
    global queue, stop

    while True:

        if stop:
            break
        else:
            # extract action from the queue until is empty or all syncThreads are busy
            while len(queue) > 0 and len(syncThreads) < MAX_SYNC_THREAD:

                queueLock.acquire()
                task = queue.popleft()
                queueLock.release()

                # skip task of non active groups
                if peerCore.groupsList[task.groupName]["status"] != "ACTIVE":
                    continue

                # assign task to a sync thread

                groupTree = peerCore.localFileTree.getGroup(task.groupName)
                fileNode = groupTree.findNode(task.fileTreePath)

                if fileNode is None:
                    # file has been removed
                    continue

                # if the file is not already in sync
                if fileNode.file.syncLock.acquire(blocking=False):
                    # Sync file if status is "D" and there are available threads
                    syncThreadsLock.acquire()

                    if fileNode.file.status == "D":
                        # start a new synchronization thread if there are less
                        # than MAX_SYNC_THREAD already active threads
                        syncThread = Thread(target=fileSharing.startFileSync,
                                            args=(fileNode.file, task.timestamp))
                        syncThread.daemon = True
                        key = task.groupName + "_" + task.fileTreePath
                        syncThreads[key] = dict()
                        syncThreads[key]["groupName"] = task.groupName
                        syncThreads[key]["state"] = SYNC_RUNNING
                        syncThread.start()

                    syncThreadsLock.release()
                    fileNode.file.syncLock.release()

                else:
                    # file is already in sync but the new task refers to a new version
                    # re-append this task in order to allow a new sync in the future
                    print("Re-appending task: ", task.toString())
                    appendTask(task)

            time.sleep(1)


def stopScheduler():
    global stop
    stop = True


def appendTask(task, checkOutdated=False):
    """
    Append a task to queue.
    If checkOutdated is True verify all the task in the queue
    and remove outdated tasks (smaller timestamp for the same file).
    Furthermore, if task is outdated don't append it.
    :param task: task to insert
    :param checkOutdated: boolean (if True enable the check)
    :return: void
    """

    global queue
    queueLock.acquire()

    # skip task of non active groups
    if peerCore.groupsList[task.groupName]["status"] != "ACTIVE":
        return


    groupTree = peerCore.localFileTree.getGroup(task.groupName)
    fileNode = groupTree.findNode(task.fileTreePath)
    if fileNode is None:
        # file has been removed
        return

    if checkOutdated:
        # remove outdated tasks and check if task is outdated or not
        deleteIndex = list()
        outdated = False

        for i in range(0, len(queue)):
            # check if task is outdated respect to task-i
            if task.isOutdated(queue[i]):
                outdated = True
                break

            # check if task-i is outdated respect to task
            if queue[i].isOutdated(task):
                deleteIndex.append(i)

        if not outdated:
            # delete outdated tasks and append the new one
            for index in deleteIndex:
                del queue[index]
            queue.append(task)

    else:
        # append task without any check
        queue.append(task)

    queueLock.release()


def removeGroupTasks(groupName):
    """
    Remove from the task queue all the tasks acting on file of a specific group.
    It's useful after a group disconnect or leave operation.
    :param groupName: name of the group
    :return: void
    """

    global queue
    queueLock.acquire()
    queue = deque([t for t in queue if t.groupName != groupName])
    queueLock.release()


def removeAllTasks():
    global queue
    queueLock.acquire()
    queue = deque()
    queueLock.release()


def removeSyncThread(key):
    syncThreadsLock.acquire()
    try:
        del syncThreads[key]
    except KeyError:
        pass
    syncThreadsLock.release()


def getThreadState(key):
    syncThreadsLock.acquire()
    try:
        state = syncThreads[key]["state"]
    except KeyError:
        state = UNDEFINED_STATE
    syncThreadsLock.release()
    return state

def stopSyncThread(key, value):
    if value == SYNC_RUNNING:
        # value is not a stopping state
        return
    syncThreadsLock.acquire()
    try:
        syncThreads[key]["state"] = value
    except KeyError:
        pass
    syncThreadsLock.release()

def stopSyncThreadIfRunning(key, value):
    if value == SYNC_RUNNING:
        # value is not a stopping state
        return
    syncThreadsLock.acquire()
    try:
        state = syncThreads[key]["state"]
        if state == SYNC_RUNNING:
            syncThreads[key]["state"] = value
    except KeyError:
        pass
    syncThreadsLock.release()


def stopSyncThreadsByGroup(groupName, value):
    syncThreadsLock.acquire()
    for thread in syncThreads.values():
        if thread["groupName"] == groupName:
            thread["state"] = value
    syncThreadsLock.release()


def stopAllSyncThreads(value):
    syncThreadsLock.acquire()
    for thread in syncThreads.values():
        thread["state"] = value
    syncThreadsLock.release()


def addedFiles(message):
    global queue

    try:
        messageFields = message.split(" ", 2)
        groupName = messageFields[1]
        filesInfo = eval(messageFields[2])

        if groupName in peerCore.groupsList:
            if peerCore.groupsList[groupName]["status"] == "ACTIVE":
                for fileInfo in filesInfo:

                    path = peerCore.scriptPath + "filesSync/" + groupName + '/'

                    treePath = fileInfo["treePath"]
                    tmp, filename = os.path.split(treePath)
                    filepath = path + "/" + treePath
                    path += tmp

                    # create the path if it doesn't exist
                    peerCore.pathCreationLock.acquire()
                    if not os.path.exists(path):
                        print("Creating the path: " + path)
                        os.makedirs(path)
                    peerCore.pathCreationLock.release()

                    # create file Object
                    file = fileManagement.File(groupName=groupName, treePath=treePath,
                                               filename=filename, filepath=filepath,
                                               filesize=fileInfo["filesize"],
                                               timestamp=fileInfo["timestamp"],
                                               status="D", previousChunks=list())

                    peerCore.localFileTree.getGroup(groupName).addNode(treePath, file)

                    # create new syncTask
                    newTask = syncTask(groupName, treePath, fileInfo["timestamp"])
                    appendTask(newTask)

                answer = "OK - FILES SUCCESSFULLY ADDED"
            else:
                answer = "ERROR - CURRENTLY I'M NOT ACTIVE"
        else:
            answer = "ERROR - GROUP DOESN'T EXIST"

    except IndexError:
        answer = "ERROR - INVALID REQUEST"

    return answer


def removedFiles(message):
    try:
        messageFields = message.split(" ", 2)
        groupName = messageFields[1]
        fileTreePaths = eval(messageFields[2])

        if groupName in peerCore.groupsList:
            if peerCore.groupsList[groupName]["status"] == "ACTIVE":
                for treePath in fileTreePaths:

                    # stop possible synchronization thread acting on the file
                    key = groupName + "_" + treePath
                    if key in syncThreads:
                        peerCore.localFileTree.getGroup(groupName).removeNode(treePath, False)
                        stopSyncThread(key, FILE_REMOVED)
                    else:
                        peerCore.localFileTree.getGroup(groupName).removeNode(treePath, True)

                answer = "OK - FILES SUCCESSFULLY REMOVED"
            else:
                answer = "ERROR - CURRENTLY I'M NOT ACTIVE"
        else:
            answer = "ERROR - GROUP DOESN'T EXIST"

    except IndexError:
        answer = "ERROR - INVALID REQUEST"

    return answer


def updatedFiles(message):
    global queue

    try:
        messageFields = message.split(" ", 2)
        groupName = messageFields[1]
        filesInfo = eval(messageFields[2])

        if groupName in peerCore.groupsList:
            if peerCore.groupsList[groupName]["status"] == "ACTIVE":
                for fileInfo in filesInfo:

                    # stop possible synchronization thread acting on the file
                    key = groupName + "_" + fileInfo["treePath"]
                    stopSyncThread(key, FILE_UPDATED)

                    fileNode = peerCore.localFileTree.getGroup(groupName).findNode(fileInfo["treePath"])

                    if fileNode is None:
                        continue

                    if fileNode.file.filestamp > fileInfo["timestamp"]:
                        continue

                    if fileNode.file.syncLock.acquire(blocking=False):

                        fileNode.file.filesize = fileInfo["filesize"]
                        fileNode.file.timestamp = fileInfo["timestamp"]
                        fileNode.file.status = "D"
                        fileNode.file.previousChunks = list()

                        fileNode.file.syncLock.release()

                        # create new syncTask
                        newTask = syncTask(groupName, fileInfo["treePath"], fileInfo["timestamp"])
                        appendTask(newTask, True)
                    else:
                        # file is currently in synchronization
                        # create a thread which will wait under the end of the synchronization
                        # and then it will update file state
                        t = Thread(target=waitSyncAndUpdate, args=(fileNode,))
                        t.daemon = True
                        t.start()

                answer = "OK - SYNC TASK LOADED"
            else:
                answer = "ERROR - CURRENTLY I'M NOT ACTIVE"
        else:
            answer = "ERROR - GROUP DOESN'T EXIST"

    except IndexError:
        answer = "ERROR - INVALID REQUEST"

    return answer


def waitSyncAndUpdate(fileNode, fileInfo):

    while not fileNode.file.syncLock.acquire(blocking=False):
        time.sleep(0.5)

    if fileNode.file.timestamp < fileInfo["timestamp"]:
        fileNode.file.filesize = fileInfo["filesize"]
        fileNode.file.timestamp = fileInfo["timestamp"]
        fileNode.file.status = "D"
        fileNode.file.previousChunks = list()

        # create new syncTask
        newTask = syncTask(fileNode.file.groupName, fileInfo["treePath"], fileInfo["timestamp"])
        appendTask(newTask, True)

    fileNode.file.syncLock.release()


