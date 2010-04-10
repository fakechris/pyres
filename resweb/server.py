import os
from itty import *
from pyres import ResQ
from pyres.failure import Failure
from views import (
    Overview, 
    Queues, 
    Queue, 
    Workers, 
    Working, 
    Failed, 
    Stats, 
    Stat, 
    Worker
)

HOST = ResQ("localhost:6379")
MY_ROOT = os.path.join(os.path.dirname(__file__), 'media')
#resq = ResQ(HOST)

@get("/")
def index(request):
    return str(Overview(HOST).render())

@get("/working/")
def working(request):
    return str(Working(HOST).render())

@get("/queues/")
def queues(request):
    return str(Queues(HOST).render())

@get('/queues/(?P<queue_id>\w+)/')
def queue(request, queue_id):
    return str(Queue(HOST, queue_id).render())

@get('/failed/')
def failed(request):
    return str(Failed(HOST).render())

@get('/workers/(?P<worker_id>\w.+)/')
def worker(request, worker_id):
    return str(Worker(HOST, worker_id).render())

@get('/workers/')
def workers(request):
    return str(Workers(HOST).render())

@get('/stats/')
def stats(request):
    raise Redirect('/stats/resque/')

@get('/stats/(?P<key>\w+)/')
def stats(request, key):
    return str(Stats(HOST, key).render())

@get('/stat/(?P<stat_id>\w.+)')
def stat(request, stat_id):
    return str(Stat(HOST, stat_id).render())

@get('/media/(?P<filename>.+)')
def my_media(request, filename):
    print filename
    #return serve_static_file(request, filename)
    #my_media.content_type = content_type(filename)
    
    return serve_static_file(request, filename, root=MY_ROOT)
    #output = static_file(filename, root=MY_ROOT)
    #return Response(output, content_type=content_type(filename))
    #return static_file(request, filename=filename, root=my_root)

#run_itty()