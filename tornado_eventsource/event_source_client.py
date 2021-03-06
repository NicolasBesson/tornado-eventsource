# -+- encoding: utf-8 -+-

# Heavily based on: https://github.com/guyzmo/event-source-library/blob/master/eventsource/client.py
# and: https://github.com/tornadoweb/tornado/blob/master/tornado/websocket.py
import logging
import collections
import re

from tornado import simple_httpclient
from tornado.ioloop import IOLoop
from tornado import httpclient, httputil
from tornado.concurrent import TracebackFuture
from tornado.netutil import Resolver
from tornado.escape import native_str


class EventSourceError(Exception):
    pass


class Event(object):
    """
    Contains a received event to be processed
    """
    def __init__(self):
        self.name = None
        self.data = None
        self.id = None
        self.retry = None

    def __repr__(self):
        return "Event<%s,%s,%s>" % (str(self.id), str(self.name), str(self.data.replace("\n", "\\n")))


class EventSourceClient(simple_httpclient._HTTPConnection):
    """
    This module opens a new connection to an eventsource server, and wait for events.
    """
    def __init__(self, io_loop, request):
        self.connect_future = TracebackFuture()
        self.read_future = None
        self.read_queue = collections.deque()
        self.events = []

        self.resolver = Resolver(io_loop=io_loop)
        super(EventSourceClient, self).__init__(
            io_loop, None, request, lambda: None, self._on_http_response,
            104857600, self.resolver)

    def _handle_event_stream(self):
        if self._timeout is not None:
            self.io_loop.remove_timeout(self._timeout)
            self._timeout = None
        self.stream.read_until_regex(b"\n\n", self.handle_stream)
        self.connect_future.set_result(self)

    def _on_http_response(self, response):
        if not self.connect_future.done():
            if response.error:
                self.connect_future.set_exception(response.error)
            else:
                self.connect_future.set_exception(EventSourceError(
                    "Non-websocket response"))

    def _on_headers(self, data):
        data = native_str(data.decode("latin1"))
        first_line, _, header_data = data.partition("\n")
        match = re.match("HTTP/1.[01] ([0-9]+) ([^\r]*)", first_line)
        assert match
        code = int(match.group(1))
        self.headers = httputil.HTTPHeaders.parse(header_data)
        self.code = code
        self.reason = match.group(2)

        if "Content-Length" in self.headers:
            if "," in self.headers["Content-Length"]:
                pieces = re.split(r',\s*', self.headers["Content-Length"])
                if any(i != pieces[0] for i in pieces):
                    raise ValueError("Multiple unequal Content-Lengths: %r" %
                                     self.headers["Content-Length"])
                self.headers["Content-Length"] = pieces[0]

        self._handle_event_stream()

    def handle_stream(self, message):
        """
        Acts on message reception
        :param message: string of an incoming message

        parse all the fields and builds an Event object that is passed to the callback function
        """
        logging.debug("handle_stream(...)")

        event = Event()
        for line in message.strip().splitlines():
            (field, value) = line.split(":", 1)
            field = field.strip()

            if field == "event":
                event.name = value.lstrip()
            elif field == "data":
                value = value.lstrip()
                if event.data is None:
                    event.data = value
                else:
                    event.data = "%s\n%s" % (event.data, value)
            elif field == "id":
                event.id = value.lstrip()
                self.last_event_id = event.id
            elif field == "retry":
                try:
                    self.retry_timeout = int(value)
                    event.retry = self.retry_timeout
                    logging.info("timeout reset: %s" % (value,))
                except ValueError:
                    pass
            elif field == "":
                logging.debug("received comment: %s" % (value,))
            else:
                raise Exception("Unknown field !")
        self.events.append(event)


def eventsource_connect(url, io_loop=None, callback=None, connect_timeout=None):
    """Client-side eventsource support.

    Takes a url and returns a Future whose result is a
    `EventSourceClient`.

    """
    if io_loop is None:
        io_loop = IOLoop.current()
    if isinstance(url, httpclient.HTTPRequest):
        assert connect_timeout is None
        request = url
        # Copy and convert the headers dict/object (see comments in
        # AsyncHTTPClient.fetch)
        request.headers = httputil.HTTPHeaders(request.headers)
    else:
        request = httpclient.HTTPRequest(url, connect_timeout=connect_timeout)
    request = httpclient._RequestProxy(
        request, httpclient.HTTPRequest._DEFAULTS)
    conn = EventSourceClient(io_loop, request)
    if callback is not None:
        io_loop.add_future(conn.connect_future, callback)
    return conn.connect_future
