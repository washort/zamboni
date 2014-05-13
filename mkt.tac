# -*- mode: python -*-
import os
import sys

from twisted.application.internet import SSLServer
from twisted.application.service import Application
from twisted.internet import reactor
from twisted.internet.endpoints import SSL4ServerEndpoint
from twisted.internet.ssl import PrivateCertificate
from twisted.python import threadpool
from twisted.web.resource import Resource
from twisted.web.static import File, loadMimeTypes
from twisted.web.server import Site
from twisted.web.wsgi import WSGIResource

from wsgi.mkt import application as wsgi_app

curdir = os.path.dirname(__file__)
FIREPLACE_ROOT = os.environ.get(
    'FIREPLACE_ROOT',
    os.path.abspath(os.path.join(curdir, '..', "fireplace")))

port = int(os.environ.get('ZAMBONI_PORT', 8000))

if 'hearth' not in os.listdir(FIREPLACE_ROOT):
    print ("Could not find Fireplace in %s. Set FIREPLACE_ROOT to the "
           "fireplace checkout directory." % (FIREPLACE_ROOT,))
    raise SystemExit
File.contentTypes = loadMimeTypes(["/etc/mime.types",
                                   "/etc/apache2/mime.types"])

application = Application("mkt")
pool = threadpool.ThreadPool()
reactor.callWhenRunning(pool.start)
reactor.addSystemEventTrigger('after', 'shutdown', pool.stop)
w = WSGIResource(reactor, pool, wsgi_app)


def fireplace(path):
    return os.path.join(FIREPLACE_ROOT, path)


class FireplaceResource(Resource):
    def getChild(self, name, request):
        if name == 'media':
            if request.postpath[0] == 'fireplace':
                f = File(fireplace("hearth/media/") +
                         "/".join(request.postpath[1:]))
            else:
                f = File("." + request.path)
            f.isLeaf = True
            return f
        elif name == 'locales':
            f = File(fireplace("hearth/locales/") + "/".join(request.postpath))
            f.isLeaf = True
            return f
        elif name in ('', 'app', 'abuse', 'category', 'debug', 'feedback',
                      'privacy-policy', 'purchases', 'partners', 'search',
                      'settings', 'terms-of-use', 'tests', 'user'):
            request.prepath = []
            request.postpath = ['server.html']
            return w
        else:
            request.prepath = []
            request.postpath.insert(0, name)
            return w

certData = os.path.join(curdir, 'vagrant/files/home/vagrant/server.pem')
certificate = PrivateCertificate.loadPEM(open(certData).read())
svc = SSLServer(port,
                Site(FireplaceResource()),
                certificate.options())

svc.setServiceParent(application)

