"""
The MIT License (MIT)
Copyright © 2018 Jean-Christophe Bos & HC² (www.hc2.fr)
"""


from   _thread  import allocate_lock, start_new_thread
from   time     import perf_counter, sleep
from   select   import select
from   queue    import Queue
import socket

# ============================================================================
# ===( XAsyncSocketsPool )====================================================
# ============================================================================

class XAsyncSocketsPoolException(Exception) :
    pass

class XAsyncSocketsPool :

    def __init__(self) :
        self._processing   = False
        self._threadsCount = 0
        self._opLock       = allocate_lock()
        self._asyncSockets = { }
        self._readList     = [ ]
        self._writeList    = [ ]
        self._handlingList = [ ]

    # ------------------------------------------------------------------------

    def _incThreadsCount(self) :
        self._opLock.acquire()
        self._threadsCount += 1
        self._opLock.release()

    # ------------------------------------------------------------------------

    def _decThreadsCount(self) :
        self._opLock.acquire()
        self._threadsCount -= 1
        self._opLock.release()

    # ------------------------------------------------------------------------

    def _addSocket(self, socket, asyncSocket) :
        self._opLock.acquire()
        ok = (not socket in self._asyncSockets)
        if ok :
            self._asyncSockets[socket] = asyncSocket
        self._opLock.release()
        return ok

    # ------------------------------------------------------------------------

    def _removeSocket(self, socket) :
        self._opLock.acquire()
        ok = (socket in self._asyncSockets)
        if ok :
            del self._asyncSockets[socket]
            if socket in self._readList :
                self._readList.remove(socket)
            if socket in self._writeList :
                self._writeList.remove(socket)
        self._opLock.release()
        return ok

    # ------------------------------------------------------------------------

    def _socketListAdd(self, socket, socketsList) :
        self._opLock.acquire()
        ok = (socket in self._asyncSockets and not socket in socketsList)
        if ok :
            socketsList.append(socket)
        self._opLock.release()
        return ok

    # ------------------------------------------------------------------------

    def _socketListRemove(self, socket, socketsList) :
        self._opLock.acquire()
        ok = (socket in self._asyncSockets and socket in socketsList)
        if ok :
            socketsList.remove(socket)
        self._opLock.release()
        return ok

    # ------------------------------------------------------------------------

    _CHECK_SEC_INTERVAL = 1.0

    def _processWaitEvents(self) :
        self._incThreadsCount()
        timeSec = perf_counter()
        while self._processing :
            try :
                rd, wr, ex = select( self._readList,
                                     self._writeList,
                                     self._readList,
                                     self._CHECK_SEC_INTERVAL )
            except :
                continue
            if not self._processing :
                break
            for socketsList in ex, wr, rd :
                for socket in socketsList :
                    asyncSocket = self._asyncSockets.get(socket, None)
                    if asyncSocket and self._socketListAdd(socket, self._handlingList) :
                        if socketsList is ex :
                            asyncSocket.OnExceptionalCondition()
                        elif socketsList is wr :
                            asyncSocket.OnReadyForWriting()
                        else :
                            asyncSocket.OnReadyForReading()
                        self._socketListRemove(socket, self._handlingList)
            sec = perf_counter()
            if sec > timeSec + self._CHECK_SEC_INTERVAL :
                timeSec = sec
                for asyncSocket in list(self._asyncSockets.values()) :
                    if asyncSocket.ExpireTimeSec and \
                       timeSec > asyncSocket.ExpireTimeSec :
                        asyncSocket._close(XClosedReason.Timeout)
        self._decThreadsCount()

    # ------------------------------------------------------------------------

    def AddAsyncSocket(self, asyncSocket) :
        try :
            socket = asyncSocket.GetSocketObj()
        except :
            raise XAsyncSocketsPoolException('AddAsyncSocket : "asyncSocket" is incorrect.')
        return self._addSocket(socket, asyncSocket)

    # ------------------------------------------------------------------------

    def RemoveAsyncSocket(self, asyncSocket) :
        try :
            socket = asyncSocket.GetSocketObj()
        except :
            raise XAsyncSocketsPoolException('RemoveAsyncSocket : "asyncSocket" is incorrect.')
        return self._removeSocket(socket)

    # ------------------------------------------------------------------------

    def NotifyNextReadyForReading(self, asyncSocket, notify) :
        try :
            socket = asyncSocket.GetSocketObj()
        except :
            raise XAsyncSocketsPoolException('NotifyNextReadyForReading : "asyncSocket" is incorrect.')
        if notify :
            self._socketListAdd(socket, self._readList)
        else :
            self._socketListRemove(socket, self._readList)

    # ------------------------------------------------------------------------

    def NotifyNextReadyForWriting(self, asyncSocket, notify) :
        try :
            socket = asyncSocket.GetSocketObj()
        except :
            raise XAsyncSocketsPoolException('NotifyNextReadyForWriting : "asyncSocket" is incorrect.')
        if notify :
            self._socketListAdd(socket, self._writeList)
        else :
            self._socketListRemove(socket, self._writeList)

    # ------------------------------------------------------------------------

    def AsyncWaitEvents(self, threadsCount=0) :
        if self._processing or self._threadsCount :
            return
        self._processing = True
        if threadsCount > 0 :
            try :
                for i in range(threadsCount) :
                    start_new_thread(self._processWaitEvents, ())
            except :
                raise XAsyncSocketsPoolException('AsyncWaitEvents : Fatal error to create new threads...')
        else :
            self._processWaitEvents()

    # ------------------------------------------------------------------------

    def StopWaitEvents(self) :
        self._processing = False
        while self._threadsCount :
            sleep(0.001)

# ============================================================================
# ===( XClosedReason )========================================================
# ============================================================================

class XClosedReason() :

    Error        = 0x00
    ClosedByHost = 0x01
    ClosedByPeer = 0x02
    Timeout      = 0x03

# ============================================================================
# ===( XAsyncSocket )=========================================================
# ============================================================================

class XAsyncSocketException(Exception) :
    pass

class XAsyncSocket :

    def __init__(self, asyncSocketsPool, socket, bufSlot=None) :
        if type(self) is XAsyncSocket :
            raise XAsyncSocketException('XAsyncSocket is an abstract class and must be implemented.')
        self._asyncSocketsPool = asyncSocketsPool
        self._socket           = socket
        self._bufSlot          = bufSlot
        self._expireTimeSec    = None
        self._state            = None
        self._onClosed         = None
        try :
            socket.settimeout(0)
            socket.setblocking(0)
            if bufSlot is not None and type(bufSlot) is not XBufferSlot :
                raise Exception()
            asyncSocketsPool.AddAsyncSocket(self)
        except :
            raise XAsyncSocketException('XAsyncSocket : Arguments are incorrects.')

    # ------------------------------------------------------------------------

    def _setExpireTimeout(self, timeoutSec) :
        try :
            if timeoutSec and timeoutSec > 0 :
                self._expireTimeSec = perf_counter() + timeoutSec
        except :
            raise XAsyncSocketException('"timeoutSec" is incorrect to set expire timeout.')

    # ------------------------------------------------------------------------

    def _removeExpireTimeout(self) :
        self._expireTimeSec = None

    # ------------------------------------------------------------------------

    def _close(self, closedReason=XClosedReason.Error, triggerOnClosed=True) :
        if self._asyncSocketsPool.RemoveAsyncSocket(self) :
            try :
                self._socket.close()
            except :
                pass
            self._socket = None
            if self._bufSlot is not None :
                self._bufSlot.Available = True
                self._bufSlot = None
            if triggerOnClosed and self._onClosed :
                try :
                    self._onClosed(self, closedReason)
                except Exception as ex :
                    raise XAsyncSocketException('Error when handling the "OnClose" event : %s' % ex)
            return True
        return False

    # ------------------------------------------------------------------------

    def GetAsyncSocketsPool(self) :
        return self._asyncSocketsPool

    # ------------------------------------------------------------------------

    def GetSocketObj(self) :
        return self._socket

    # ------------------------------------------------------------------------

    def Close(self) :
        return self._close(XClosedReason.ClosedByHost)

    # ------------------------------------------------------------------------

    def OnReadyForReading(self) :
        pass

    # ------------------------------------------------------------------------

    def OnReadyForWriting(self) :
        pass

    # ------------------------------------------------------------------------

    def OnExceptionalCondition(self) :
        self._close()

    # ------------------------------------------------------------------------

    @property
    def ExpireTimeSec(self) :
        return self._expireTimeSec

    @property
    def OnClosed(self) :
        return self._onClosed
    @OnClosed.setter
    def OnClosed(self, value) :
        self._onClosed = value

    @property
    def State(self) :
        return self._state
    @State.setter
    def State(self, value) :
        self._state = value

# ============================================================================
# ===( XAsyncTCPServer )======================================================
# ============================================================================

class XAsyncTCPServerException(Exception) :
    pass

class XAsyncTCPServer(XAsyncSocket) :

    @staticmethod
    def Create(asyncSocketsPool, srvAddr, srvBacklog=256, recvBufSlots=None) :
        srvSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try :
            srvSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srvSocket.bind(srvAddr)
            srvSocket.listen(srvBacklog)
        except :
            raise XAsyncTCPServerException('Create : Error to binding the TCP server on this address.')
        if not recvBufSlots :
            recvBufSlots = XBufferSlots(256, 4096, keepAlloc=True)
        xAsyncTCPServer = XAsyncTCPServer( asyncSocketsPool,
                                           srvSocket,
                                           srvAddr,
                                           recvBufSlots )
        asyncSocketsPool.NotifyNextReadyForReading(xAsyncTCPServer, True)
        return xAsyncTCPServer

    # ------------------------------------------------------------------------

    def __init__(self, asyncSocketsPool, srvSocket, srvAddr, recvBufSlots) :
        try :
            super().__init__(asyncSocketsPool, srvSocket)
            self._srvAddr          = srvAddr
            self._recvBufSlots     = recvBufSlots
            self._onClientAccepted = None
        except :
            raise XAsyncTCPServerException('Error to creating XAsyncTCPServer, arguments are incorrects.')

    # ------------------------------------------------------------------------

    def OnReadyForReading(self) :
        try :
            cliSocket, cliAddr = self._socket.accept()
        except :
            return
        bufSlot = self._recvBufSlots.GetAvailableSlot()
        if not bufSlot or not self._onClientAccepted :
            cliSocket.close()
            return
        asyncTCPCli = XAsyncTCPClient( self._asyncSocketsPool,
                                       cliSocket,
                                       self._srvAddr,
                                       cliAddr,
                                       bufSlot )
        try :
            self._onClientAccepted(self, asyncTCPCli)
        except Exception as ex :
            asyncTCPCli._close()
            raise XAsyncTCPServerException('Error when handling the "OnClientAccepted" event : %s' % ex)
        self._asyncSocketsPool.NotifyNextReadyForWriting(asyncTCPCli, True)

    # ------------------------------------------------------------------------

    @property
    def SrvAddr(self) :
        return self._srvAddr

    @property
    def OnClientAccepted(self) :
        return self._onClientAccepted
    @OnClientAccepted.setter
    def OnClientAccepted(self, value) :
        self._onClientAccepted = value

# ============================================================================
# ===( XAsyncTCPClient )======================================================
# ============================================================================

class XAsyncTCPClientException(Exception) :
    pass

class XAsyncTCPClient(XAsyncSocket) :

    @staticmethod
    def Create(asyncSocketsPool, srvAddr, connectTimeout=5, recvbufLen=4096) :
        try :
            size    = max(256, int(recvbufLen))
            bufSlot = XBufferSlot(size=size, keepAlloc=False)
        except :
            raise XAsyncTCPClientException('Create : "recvbufLen" is incorrect.')
        cliSocket   = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        asyncTCPCli = XAsyncTCPClient( asyncSocketsPool,
                                       cliSocket,
                                       srvAddr,
                                       None,
                                       bufSlot )
        try :
            errno = cliSocket.connect_ex(srvAddr)
            if errno == 0 or errno == 36 :
                asyncTCPCli._setExpireTimeout(connectTimeout)
                asyncSocketsPool.NotifyNextReadyForWriting(asyncTCPCli, True)
                return asyncTCPCli
        except :
            pass
        asyncTCPCli._close()
        return None

    # ------------------------------------------------------------------------

    def __init__(self, asyncSocketsPool, cliSocket, srvAddr, cliAddr, bufSlot) :
        try :
            super().__init__(asyncSocketsPool, cliSocket, bufSlot)
            self._srvAddr          = srvAddr
            self._cliAddr          = cliAddr if cliAddr else ('0.0.0.0', 0)
            self._onFailsToConnect = None
            self._onConnected      = None
            self._onLineRecv       = None
            self._onDataRecv       = None
            self._onCanSend        = None
            self._sizeToRead       = None
            self._rdLinePos        = None
            self._rdBufView        = None
            self._wrBufView        = memoryview(b'')
            self._socketOpened     = (cliAddr is not None)
        except :
            raise XAsyncTCPClientException('Error to creating XAsyncTCPClient, arguments are incorrects.')

    # ------------------------------------------------------------------------

    def Close(self) :
        try :
            self._socket.shutdown(socket.SHUT_RDWR)
        except :
            pass
        return self._close(XClosedReason.ClosedByHost)

    # ------------------------------------------------------------------------

    def OnReadyForReading(self) :
        if self._rdLinePos is not None :
            # In the context of reading a line,
            while True :
                try :
                    b = self._socket.recv(1)
                except :
                    break
                if b :
                    if b == b'\n' :
                        lineLen = self._rdLinePos 
                        self._rdLinePos = None
                        self._asyncSocketsPool.NotifyNextReadyForReading(self, False)
                        self._removeExpireTimeout()
                        if self._onLineRecv :
                            try :
                                line = self._bufSlot.Buffer[:lineLen].decode()
                            except :
                                break
                            try :
                                self._onLineRecv(self, line)
                            except Exception as ex :
                                raise XAsyncTCPClientException('Error when handling the "OnLineRecv" event : %s' % ex)
                        break
                    elif b != b'\r' :
                        if self._rdLinePos < self._bufSlot.Size :
                            self._bufSlot.Buffer[self._rdLinePos] = ord(b)
                            self._rdLinePos += 1
                        else :
                            self._close()
                            break
                else :
                    self._close(XClosedReason.ClosedByPeer)
                    break
        else :
            # In the context of reading data,
            try :
                n = self._socket.recv_into(self._rdBufView)
            except :
                self._close()
                return
            self._rdBufView = self._rdBufView[n:]
            if n > 0 :
                if not self._sizeToRead or not self._rdBufView :
                    self._asyncSocketsPool.NotifyNextReadyForReading(self, False)
                    self._removeExpireTimeout()
                    if self._onDataRecv :
                        size = self._sizeToRead if self._sizeToRead else n
                        try :
                            self._onDataRecv(self, memoryview(self._bufSlot.Buffer)[:size])
                        except Exception as ex :
                            raise XAsyncTCPClientException('Error when handling the "OnDataRecv" event : %s' % ex)
            else :
                self._close(XClosedReason.ClosedByPeer)

    # ------------------------------------------------------------------------

    def OnReadyForWriting(self) :
        if not self._socketOpened :
            if self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) :
                self._close(XClosedReason.Error, triggerOnClosed=False)
                if self._onFailsToConnect :
                    try :
                        self._onFailsToConnect(self)
                    except Exception as ex :
                        raise XAsyncTCPClientException('Error when handling the "OnFailsToConnect" event : %s' % ex)
                return
            self._socketOpened = True
            self._cliAddr      = self._socket.getsockname()
            self._removeExpireTimeout()
            if self._onConnected :
                try :
                    self._onConnected(self)
                except Exception as ex :
                    raise XAsyncTCPClientException('Error when handling the "OnConnected" event : %s' % ex)
        if self._wrBufView :
            try :
                n = self._socket.send(self._wrBufView)
            except :
                self._close()
                return
            self._wrBufView = self._wrBufView[n:]
            if self._wrBufView :
                return
        self._asyncSocketsPool.NotifyNextReadyForWriting(self, False)
        if self._onCanSend :
            try :
                self._onCanSend(self)
            except Exception as ex :
                raise XAsyncTCPClientException('Error when handling the "OnCanSend" event : %s' % ex)

    # ------------------------------------------------------------------------

    def AsyncRecvLine(self, timeoutSec=None) :
        if self._socket :
            self._setExpireTimeout(timeoutSec)
            self._rdLinePos = 0
            self._asyncSocketsPool.NotifyNextReadyForReading(self, True)
            return True
        return False

    # ------------------------------------------------------------------------

    def AsyncRecvData(self, size=None, timeoutSec=None) :
        if self._socket :
            if size :
                try :
                    size = int(size)
                except :
                    raise XAsyncTCPClientException('AsyncRecvData : "size" is incorrect.')
            if not size or size < 0 :
                self._sizeToRead = None
                size             = self._bufSlot.Size
            elif size > self._bufSlot.Size :
                raise XAsyncTCPClientException('AsyncRecvData : "size" must be less or equal to buffer size.')
            else :
                self._sizeToRead = size
            self._setExpireTimeout(timeoutSec)
            self._rdBufView = memoryview(self._bufSlot.Buffer)[:size]
            self._asyncSocketsPool.NotifyNextReadyForReading(self, True)
            return True
        return False

    # ------------------------------------------------------------------------

    def AsyncSendData(self, data) :
        if self._socket :
            try :
                if bytes([data[0]]) :
                    if self._wrBufView :
                        self._wrBufView = memoryview(self._wrBufView.tobytes() + data)
                    else :
                        self._wrBufView = memoryview(data)
                    self._asyncSocketsPool.NotifyNextReadyForWriting(self, True)
                    return True
            except :
                pass
            raise XAsyncTCPClientException('AsyncSendData : "data" is incorrect.')
        return False

    # ------------------------------------------------------------------------

    @property
    def SrvAddr(self) :
        return self._srvAddr

    @property
    def CliAddr(self) :
        return self._cliAddr

    @property
    def OnFailsToConnect(self) :
        return self._onFailsToConnect
    @OnFailsToConnect.setter
    def OnFailsToConnect(self, value) :
        self._onFailsToConnect = value

    @property
    def OnConnected(self) :
        return self._onConnected
    @OnConnected.setter
    def OnConnected(self, value) :
        self._onConnected = value

    @property
    def OnLineRecv(self) :
        return self._onLineRecv
    @OnLineRecv.setter
    def OnLineRecv(self, value) :
        self._onLineRecv = value

    @property
    def OnDataRecv(self) :
        return self._onDataRecv
    @OnDataRecv.setter
    def OnDataRecv(self, value) :
        self._onDataRecv = value

    @property
    def OnCanSend(self) :
        return self._onCanSend
    @OnCanSend.setter
    def OnCanSend(self, value) :
        self._onCanSend = value

# ============================================================================
# ===( XAsyncUDPDatagram )====================================================
# ============================================================================

class XAsyncUDPDatagramException(Exception) :
    pass

class XAsyncUDPDatagram(XAsyncSocket) :

    @staticmethod
    def Create(asyncSocketsPool, localAddr=None, recvbufLen=4096, broadcast=False) :
        udpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if broadcast :
            udpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        openRecv = (localAddr is not None)
        if openRecv :
            try :
                udpSocket.bind(localAddr)
            except :
                raise XAsyncUDPDatagramException('Create : Error to binding the UDP Datagram local address.')
            try :
                size    = max(256, int(recvbufLen))
                bufSlot = XBufferSlot(size=size, keepAlloc=False)
            except :
                raise XAsyncUDPDatagramException('Create : "recvbufLen" is incorrect.')
        else :
            bufSlot = None
        xAsyncUDPDatagram = XAsyncUDPDatagram(asyncSocketsPool, udpSocket, bufSlot)
        if openRecv :
            asyncSocketsPool.NotifyNextReadyForReading(xAsyncUDPDatagram, True)
        asyncSocketsPool.NotifyNextReadyForWriting(xAsyncUDPDatagram, True)
        return xAsyncUDPDatagram

    # ------------------------------------------------------------------------

    def __init__(self, asyncSocketsPool, udpSocket, bufSlot) :
        try :
            super().__init__(asyncSocketsPool, udpSocket, bufSlot)
            self._wrDgramQueue  = Queue()
            self._onFailsToSend = None
            self._onCanSend     = None
            self._onRecv        = None
        except :
            raise XAsyncUDPDatagramException('Error to creating XAsyncUDPDatagram, arguments are incorrects.')

    # ------------------------------------------------------------------------

    def OnReadyForReading(self) :
        try :
            n, remoteAddr = self._socket.recvfrom_into(self._bufSlot.Buffer)
        except :
            return
        if self._onRecv :
            try :
                datagram = memoryview(self._bufSlot.Buffer)[:n]
                self._onRecv(self, remoteAddr, datagram)
            except Exception as ex :
                raise XAsyncUDPDatagramException('Error when handling the "OnRecv" event : %s' % ex)


    # ------------------------------------------------------------------------

    def OnReadyForWriting(self) :
        if not self._wrDgramQueue.empty() :
            datagram   = None
            remoteAddr = ('0.0.0.0', 0)
            try :
                datagram, remoteAddr = self._wrDgramQueue.get_nowait()
                self._socket.sendto(datagram, remoteAddr)
            except :
                if self._onFailsToSend :
                    try :
                        self._onFailsToSend(self, datagram, remoteAddr)
                    except Exception as ex :
                        raise XAsyncUDPDatagramException('Error when handling the "OnFailsToSend" event : %s' % ex)
            if not self._wrDgramQueue.empty() :
                return
        self._asyncSocketsPool.NotifyNextReadyForWriting(self, False)
        if self._onCanSend :
            try :
                self._onCanSend(self)
            except Exception as ex :
                raise XAsyncUDPDatagramException('Error when handling the "OnCanSend" event : %s' % ex)

    # ------------------------------------------------------------------------

    def AsyncSendDatagram(self, datagram, remoteAddr) :
        if self._socket :
            try :
                if bytes([datagram[0]]) and len(remoteAddr) == 2 :
                    self._wrDgramQueue.put_nowait( (datagram, remoteAddr) )
                    self._asyncSocketsPool.NotifyNextReadyForWriting(self, True)
                    return True
            except :
                pass
            raise XAsyncUDPDatagramException('AsyncSendDatagram : Arguments are incorrects.')
        return False

    # ------------------------------------------------------------------------

    @property
    def LocalAddr(self) :
        try :
            return self._socket.getsockname()
        except :
            return ('0.0.0.0', 0)

    @property
    def OnRecv(self) :
        return self._onRecv
    @OnRecv.setter
    def OnRecv(self, value) :
        self._onRecv = value

    @property
    def OnFailsToSend(self) :
        return self._onFailsToSend
    @OnFailsToSend.setter
    def OnFailsToSend(self, value) :
        self._onFailsToSend = value

    @property
    def OnCanSend(self) :
        return self._onCanSend
    @OnCanSend.setter
    def OnCanSend(self, value) :
        self._onCanSend = value

# ============================================================================
# ===( XBufferSlot )==========================================================
# ============================================================================

class XBufferSlot :

    def __init__(self, size, keepAlloc=True) :
        self._available = True
        self._size      = size
        self._keepAlloc = keepAlloc
        self._buffer    = bytearray(size) if keepAlloc else None

    @property
    def Available(self) :
        return self._available
    @Available.setter
    def Available(self, value) :
        if value and not self._keepAlloc :
            self._buffer = None
        self._available = value

    @property
    def Size(self) :
        return self._size

    @property
    def Buffer(self) :
        self._available = False
        if self._buffer is None :
            self._buffer = bytearray(self._size)
        return self._buffer

# ============================================================================
# ===( XBufferSlots )=========================================================
# ============================================================================

class XBufferSlots :

    def __init__(self, slotsCount, slotsSize, keepAlloc=True) :
        self._slotsCount = slotsCount
        self._slotsSize  = slotsSize
        self._slots      = [ ]
        self._lock       = allocate_lock()
        for i in range(slotsCount) :
            self._slots.append(XBufferSlot(slotsSize, keepAlloc))

    def GetAvailableSlot(self) :
        ret = None
        self._lock.acquire()
        for slot in self._slots :
            if slot.Available :
                slot.Available = False
                ret = slot
                break
        self._lock.release()
        return ret

    @property
    def SlotsCount(self) :
        return self.slotsCount

    @property
    def SlotsSize(self) :
        return self.slotsSize

    @property
    def Slots(self) :
        return self._slots

# ============================================================================
# ============================================================================
# ============================================================================
