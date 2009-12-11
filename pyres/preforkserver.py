# Copyright (c) 2005 Allan Saddi <allan@saddi.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
# $Id$

__author__ = 'Allan Saddi <allan@saddi.com>'
__version__ = '$Revision$'

# Prefork Server modified from flup, remove sock dependence and add timeout to do job
# Chris Song <chris__song@hotmail.com>

import sys
import os
import socket
import select
import errno
import signal
import random
import time

try:
    import fcntl
except ImportError:
    def setCloseOnExec(sock):
        pass
else:
    def setCloseOnExec(sock):
        fcntl.fcntl(sock.fileno(), fcntl.F_SETFD, fcntl.FD_CLOEXEC)

class PreforkServer(object):
    # timeout -- child select timeout
    def __init__(self, minSpare=1, maxSpare=5, maxChildren=2,
                 maxRequests=0, timeout=1,
                 jobClass=None, jobArgs=()):
        self._minSpare = minSpare
        self._maxSpare = maxSpare
        self._maxChildren = max(maxSpare, maxChildren)
        self._maxRequests = maxRequests
        self._timeout = timeout
        
        self._jobClass = jobClass
        self._jobArgs = jobArgs

        self._children = {}

        self._children_to_purge = []
        self._last_purge = 0

        if minSpare < 1:
            raise ValueError("minSpare must be at least 1!")
        if maxSpare < minSpare:
            raise ValueError("maxSpare must be greater than, or equal to, minSpare!")

    def run(self):
        # Set up signal handlers.
        self._keepGoing = True
        self._hupReceived = False
        self._installSignalHandlers()

        # Main loop.
        while self._keepGoing:
            # Maintain minimum number of children. Note that we are checking
            # the absolute number of children, not the number of "available"
            # children. We explicitly test against _maxSpare to maintain
            # an *optimistic* absolute minimum. The number of children will
            # always be in the range [_maxSpare, _maxChildren].
            while len(self._children) < self._maxSpare:
                if not self._spawnChild(): break
            
            # Wait on any socket activity from live children.
            r = [x['file'] for x in self._children.values()
                 if x['file'] is not None]
            
            if len(r) == len(self._children) and not self._children_to_purge:
                timeout = None
            else:
                # There are dead children that need to be reaped, ensure
                # that they are by timing out, if necessary. Or there are some
                # children that need to die.
                timeout = 2
                
            w = []
            if (time.time() > self._last_purge + 10):
                w = [x for x in self._children_to_purge if x.fileno() != -1]
            try:
                r, w, e = select.select(r, w, [], timeout)
            except select.error, e:
                if e[0] != errno.EINTR:
                    raise
                
            # Scan child sockets and tend to those that need attention.
            for child in r:
                # Receive status byte.
                try:
                    state = child.recv(1)
                except socket.error, e:
                    if e[0] in (errno.EAGAIN, errno.EINTR):
                        # Guess it really didn't need attention?
                        continue
                    raise
                # Try to match it with a child. (Do we need a reverse map?)
                for pid,d in self._children.items():
                    if child is d['file']:
                        if state:
                            # Set availability status accordingly.
                            self._children[pid]['avail'] = state != '\x00'
                        else:
                            # Didn't receive anything. Child is most likely
                            # dead.
                            d = self._children[pid]
                            d['file'].close()
                            d['file'] = None
                            d['avail'] = False
                            
            for child in w:
                # purging child
                child.send('bye, bye')
                del self._children_to_purge[self._children_to_purge.index(child)]
                self._last_purge = time.time()

                # Try to match it with a child. (Do we need a reverse map?)
                for pid,d in self._children.items():
                    if child is d['file']:
                        d['file'].close()
                        d['file'] = None
                        d['avail'] = False
                break

            # Reap children.
            self._reapChildren()

            # See who and how many children are available.
            availList = filter(lambda x: x[1]['avail'], self._children.items())
            avail = len(availList)
            
            if avail < self._minSpare:
                # Need to spawn more children.
                while avail < self._minSpare and \
                      len(self._children) < self._maxChildren:
                    if not self._spawnChild(): break
                    avail += 1
            elif avail > self._maxSpare:
                # Too many spares, kill off the extras.
                pids = [x[0] for x in availList]
                pids.sort()
                pids = pids[self._maxSpare:]
                for pid in pids:
                    d = self._children[pid]
                    d['file'].close()
                    d['file'] = None
                    d['avail'] = False
                    
        # Clean up all child processes.
        self._cleanupChildren()

        # Restore signal handlers.
        self._restoreSignalHandlers()

        # Return bool based on whether or not SIGHUP was received.
        return self._hupReceived
    
    def _cleanupChildren(self):
        """
        Closes all child sockets (letting those that are available know
        that it's time to exit). Sends SIGINT to those that are currently
        processing (and hopes that it finishses ASAP).

        Any children remaining after 10 seconds is SIGKILLed.
        """
        # Let all children know it's time to go.
        for pid,d in self._children.items():
            if d['file'] is not None:
                d['file'].close()
                d['file'] = None
            if not d['avail']:
                # Child is unavailable. SIGINT it.
                try:
                    os.kill(pid, signal.SIGINT)
                except OSError, e:
                    if e[0] != errno.ESRCH:
                        raise

        def alrmHandler(signum, frame):
            pass

        # Set up alarm to wake us up after 10 seconds.
        oldSIGALRM = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, alrmHandler)
        signal.alarm(10)

        # Wait for all children to die.
        while len(self._children):
            try:
                pid, status = os.wait()
            except OSError, e:
                if e[0] in (errno.ECHILD, errno.EINTR):
                    break
            if self._children.has_key(pid):
                del self._children[pid]

        signal.alarm(0)
        signal.signal(signal.SIGALRM, oldSIGALRM)

        # Forcefully kill any remaining children.
        for pid in self._children.keys():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError, e:
                if e[0] != errno.ESRCH:
                    raise

    def _reapChildren(self):
        """Cleans up self._children whenever children die."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except OSError, e:
                if e[0] == errno.ECHILD:
                    break
                raise
            if pid <= 0:
                break
            if self._children.has_key(pid): # Sanity check.
                if self._children[pid]['file'] is not None:
                    self._children[pid]['file'].close()
                    self._children[pid]['file'] = None
                del self._children[pid]
                
    def _spawnChild(self):
        """
        Spawn a single child. Returns True if successful, False otherwise.
        """
        # This socket pair is used for very simple communication between
        # the parent and its children.
        parent, child = socket.socketpair()
        parent.setblocking(0)
        setCloseOnExec(parent)
        child.setblocking(0)
        setCloseOnExec(child)
        try:
            pid = os.fork()
        except OSError, e:
            if e[0] in (errno.EAGAIN, errno.ENOMEM):
                return False # Can't fork anymore.
            raise
        if not pid:
            # Child
            child.close()
            # Put child into its own process group.
            pid = os.getpid()
            os.setpgid(pid, pid)
            # Restore signal handlers.
            self._restoreSignalHandlers()
            # Close copies of child sockets.
            for f in [x['file'] for x in self._children.values()
                      if x['file'] is not None]:
                f.close()
            self._children = {}
            try:
                # Enter main loop.
                self._child(parent)
            except KeyboardInterrupt:
                pass
            sys.exit(0)
        else:
            # Parent
            parent.close()
            d = self._children[pid] = {}
            d['file'] = child
            d['avail'] = True
            return True  
        
    def _isClientAllowed(self, addr):
        """Override to provide access control."""
        return True

    def _notifyParent(self, parent, msg):
        """Send message to parent, ignoring EPIPE and retrying on EAGAIN"""
        while True:
            try:
                parent.send(msg)
                return True
            except socket.error, e:
                if e[0] == errno.EPIPE:
                    return False # Parent is gone
                if e[0] == errno.EAGAIN:
                    # Wait for socket change before sending again
                    select.select([], [parent], [])
                else:
                    raise
                
    def _child(self, parent):
        """Main loop for children."""
        requestCount = 0

        # Re-seed random module
        preseed = ''
        # urandom only exists in Python >= 2.4
        if hasattr(os, 'urandom'):
            try:
                preseed = os.urandom(16)
            except NotImplementedError:
                pass
        # Have doubts about this. random.seed will just hash the string
        random.seed('%s%s%s' % (preseed, os.getpid(), time.time()))
        del preseed

        while True:
            # Wait for any activity on the main socket or parent socket.
            r, w, e = select.select([parent], [], [], self._timeout)

            for f in r:
                # If there's any activity on the parent socket, it
                # means the parent wants us to die or has died itself.
                # Either way, exit.
                if f is parent:
                    return

            # Otherwise, there's some job to do...

            # Notify parent we're no longer available.
            self._notifyParent(parent, '\x00')

            # Do the job.
            self._jobClass(*self._jobArgs).run()
            
            # If we've serviced the maximum number of requests, exit.
            if self._maxRequests > 0:
                requestCount += 1
                if requestCount >= self._maxRequests:
                    break
                
            # Tell parent we're free again.
            if not self._notifyParent(parent, '\xff'):
                return # Parent is gone.  


    # Signal handlers

    def _hupHandler(self, signum, frame):
        self._keepGoing = False
        self._hupReceived = True

    def _intHandler(self, signum, frame):
        self._keepGoing = False

    def _chldHandler(self, signum, frame):
        # Do nothing (breaks us out of select and allows us to reap children).
        pass

    def _usr1Handler(self, signum, frame):
        self._children_to_purge = [x['file'] for x in self._children.values()
                                   if x['file'] is not None]

    def _installSignalHandlers(self):
        supportedSignals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, 'SIGHUP'):
            supportedSignals.append(signal.SIGHUP)
        if hasattr(signal, 'SIGUSR1'):
            supportedSignals.append(signal.SIGUSR1)

        self._oldSIGs = [(x,signal.getsignal(x)) for x in supportedSignals]

        for sig in supportedSignals:
            if hasattr(signal, 'SIGHUP') and sig == signal.SIGHUP:
                signal.signal(sig, self._hupHandler)
            elif hasattr(signal, 'SIGUSR1') and sig == signal.SIGUSR1:
                signal.signal(sig, self._usr1Handler)
            else:
                signal.signal(sig, self._intHandler)

    def _restoreSignalHandlers(self):
        """Restores previous signal handlers."""
        for signum,handler in self._oldSIGs:
            signal.signal(signum, handler)
            
if __name__ == '__main__':
    PreforkServer(maxChildren=10).run()
