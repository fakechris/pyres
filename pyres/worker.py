from pyres.exceptions import NoQueueError
from pyres.job import Job
from pyres import ResQ, Stat
import signal
import datetime
import os, sys
import time
import simplejson

class Worker(object):
    def __init__(self, queues=[], 
                 server="localhost:6379", password=None,
                 verbose=False):
        self.queues = queues
        self.validate_queues()
        self._shutdown = False
        self.child = None
        self.pid = os.getpid()
        self.verbose = verbose
        if isinstance(server,basestring):
            self.resq = ResQ(server=server, password=password)
        elif isinstance(server, ResQ):
            self.resq = server
        else:
            raise Exception("Bad server argument")
        
    
    def validate_queues(self):
        if not self.queues:
            raise NoQueueError("Please give each worker at least one queue.")
    
    def register_worker(self):
        self.resq.redis.sadd('resque:workers',str(self))
        self.started = datetime.datetime.now()
    
    def _set_started(self, time):
        if time:
            self.resq.redis.set("resque:worker:%s:started" % self, time.strftime('%Y-%m-%d %H:%M:%S'))
        else:
            self.resq.redis.delete("resque:worker:%s:started" % self)
            
    def _get_started(self):
        datestring = self.resq.redis.get("resque:worker:%s:started" % self)
        ds = None
        if datestring:
            ds = datetime.datetime.strptime(datestring, '%Y-%m-%d %H:%M:%S')
        return ds
    
    started = property(_get_started, _set_started)
    
    def unregister_worker(self):
        self.resq.redis.srem('resque:workers',str(self))
        self.resq.redis.delete('resque:worker:%s:started' % str(self))
        Stat("processed:%s" % str(self), self.resq).clear()
        Stat("failed:%s" % str(self), self.resq).clear()
        self.started = None
    
    def startup(self):
        self.register_signal_handlers()
        self.register_worker()
    
    def register_signal_handlers(self):
        signal.signal(signal.SIGTERM, self.shutdown_all)
        signal.signal(signal.SIGINT, self.shutdown_all)
        signal.signal(signal.SIGQUIT, self.schedule_shutdown)
        signal.signal(signal.SIGUSR1, self.kill_child)
    
    def shutdown_all(self, signum, frame):
        self.schedule_shutdown(signum, frame)
        self.kill_child(signum, frame)
    
    def schedule_shutdown(self, signum, frame):
        self._shutdown = True
    
    def kill_child(self, signum, frame):
        if self.child:
            print "Killing child at %s" % self.child
            os.kill(self.child, signal.SIGKILL)
            
    def __str__(self):
        if getattr(self,'id', None):
            return self.id
        hostname = os.uname()[1]
        return '%s:%s:%s' % (hostname, self.pid, ','.join(self.queues))
         
    def batch_work(self, interval=5, max_requests=100):
        self.register_signal_handlers()        
        while True:
            if self._shutdown:
                print 'shutdown scheduled'
                break
            
            self.child = os.fork()
            if self.child:
                print 'Forked %s at %s' % (self.child, datetime.datetime.now())
                try:
                    os.waitpid(self.child, 0)
                except OSError, ose:
                    import errno
                    if ose.errno != errno.EINTR:
                        raise ose
                #os.wait()
                print 'Done waiting'
            else:
                self.pid = os.getpid() # child process should update pid
                self.register_worker()
                requestCount = 0
                while requestCount < max_requests:
                    job = self.reserve()
                    if job:
                        requestCount += 1
                        if self.verbose:
                            print 'Processing %s since %s' % (job._queue, datetime.datetime.now())
                        self.process(job)
                    else:
                        if interval == 0:
                            break
                        time.sleep(interval)
                self.unregister_worker()
                os._exit(0)
            self.child = None            
            
    def work(self, interval=5):
        self.startup()
        while True:
            if self._shutdown:
                print 'shutdown scheduled'
                break
            job = self.reserve()
            if job:
                print "got: %s" % job
                self.child = os.fork()
                if self.child:
                    print 'Forked %s at %s' % (self.child, datetime.datetime.now())
                    try:
                        os.waitpid(self.child, 0)
                    except OSError, ose:
                        import errno
                        if ose.errno != errno.EINTR:
                            raise ose
                    #os.wait()
                    print 'Done waiting'
                else:
                    print 'Processing %s since %s' % (job._queue, datetime.datetime.now())
                    self.process(job)
                    os._exit(0)
                self.child = None
            else:
                if interval == 0:
                    break
                time.sleep(interval)
        self.unregister_worker()
    
    def process(self, job=None):
        if not job:
            job = self.reserve()
        try:
            self.working_on(job)
            job.perform()
        except Exception, e:
            print "%s failed: %s" % (job, e)
            job.fail(e)
            self.failed()
        else:
            if self.verbose:
                print "done: %s" % job
        finally:
            self.done_working()
    
    def reserve(self):
        for q in self.queues:
            #print "Checking %s" % q
            job = Job.reserve(q, self.resq, self.__str__())
            if job:
                if self.verbose:
                    print "Found job on %s" % q
                return job
    
    def working_on(self, job):
        if self.verbose:
            print 'marking as working on'
        data = {
            'queue': job._queue,
            'run_at': str(datetime.datetime.now()),
            'payload': job._payload
        }
        data = simplejson.dumps(data)
        self.resq.redis["resque:worker:%s" % str(self)] = data
        if self.verbose:
            print "worker:%s" % str(self)
        print self.resq.redis["resque:worker:%s" % str(self)]
    
    def done_working(self):
        if self.verbose:
            print 'done working'
        self.processed()
        self.resq.redis.delete("resque:worker:%s" % str(self))
    
    def processed(self):
        total_processed = Stat("processed", self.resq)
        worker_processed = Stat("processed:%s" % str(self), self.resq)
        total_processed.incr()
        worker_processed.incr()
    
    def get_processed(self):
        return Stat("processed:%s" % str(self), self.resq).get()
    
    def failed(self):
        Stat("failed", self.resq).incr()
        Stat("failed:%s" % self, self.resq).incr()
        
    def get_failed(self):
        return Stat("failed:%s" % self, self.resq).get()
    
    def job(self):
        data = self.resq.redis.get("resque:worker:%s" % self)
        if data:
            return ResQ.decode(data)
        return {}

    
    def processing(self):
        return self.job()
    
    def state(self):
        return 'working' if self.resq.redis.exists('resque:worker:%s' % self) else 'idle'

    @classmethod
    def batch_run(cls, queues, server, interval=5, max_requests=100):
        worker = cls(queues=queues, server=server)
        worker.batch_work(interval=interval, max_requests=max_requests)
        
    @classmethod
    def run(cls, queues, server):
        worker = cls(queues=queues, server=server)
        worker.work()
    
    @classmethod
    def all(cls, host="localhost:6379"):
        if isinstance(host,basestring):
            resq = ResQ(host)
        elif isinstance(host, ResQ):
            resq = host
        return [Worker.find(w,resq) for w in resq.redis.smembers('resque:workers')]
    
    @classmethod
    def working(cls, host):
        if isinstance(host, basestring):
            resq = ResQ(host)
        elif isinstance(host, ResQ):
            resq = host
        total = []
        for key in Worker.all(host):            
            total.append('resque:worker:%s' % key)
        names = []
        for key in total:
            value = resq.redis.get(key)
            if value:
                w = Worker.find(key[14:], resq) #resque:worker:
                names.append(w)
        return names
    
    @classmethod
    def find(cls, worker_id, resq):
        if Worker.exists(worker_id, resq):
            queues = worker_id.split(':')[-1].split(',')
            worker = cls(queues,resq)
            worker.id = worker_id
            return worker
        else:
            return None
    
    @classmethod
    def exists(cls, worker_id, resq):
        return resq.redis.sismember('resque:workers', worker_id)
    

if __name__ == "__main__":
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option("-q", dest="queue_list")
    parser.add_option("-s", dest="server", default="localhost:6379")
    (options,args) = parser.parse_args()
    if not options.queue_list:
        parser.print_help()
        parser.error("Please give each worker at least one queue.")
    queues = options.queue_list.split(',')
    Worker.run(queues, options.server)
