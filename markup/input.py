# -*- coding: utf-8 -*-
#
# Copyright (C) 2006 Edgewall Software
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution. The terms
# are also available at http://markup.edgewall.org/wiki/License.
#
# This software consists of voluntary contributions made by many
# individuals. For the exact contribution history, see the revision
# history and logs, available at http://markup.edgewall.org/log/.

from xml.parsers import expat
try:
    frozenset
except NameError:
    from sets import ImmutableSet as frozenset
import HTMLParser as html
import htmlentitydefs
from StringIO import StringIO

from markup.core import Attributes, Markup, QName, Stream


class ParseError(Exception):
    """Exception raised when fatal syntax errors are found in the input being
    parsed."""

    def __init__(self, message, filename='<string>', lineno=-1, offset=-1):
        Exception.__init__(self, message)
        self.filename = filename
        self.lineno = lineno
        self.offset = offset


class XMLParser(object):
    """Generator-based XML parser based on roughly equivalent code in
    Kid/ElementTree.
    
    The parsing is initiated by iterating over the parser object:
    
    >>> parser = XMLParser(StringIO('<root id="2"><child>Foo</child></root>'))
    >>> for kind, data, pos in parser:
    ...     print kind, data
    START (u'root', [(u'id', u'2')])
    START (u'child', [])
    TEXT Foo
    END child
    END root
    """

    def __init__(self, source, filename=None):
        """Initialize the parser for the given XML text.
        
        @param source: the XML text as a file-like object
        @param filename: the name of the file, if appropriate
        """
        self.source = source
        self.filename = filename

        # Setup the Expat parser
        parser = expat.ParserCreate('utf-8', '}')
        parser.buffer_text = True
        parser.returns_unicode = True
        parser.StartElementHandler = self._handle_start
        parser.EndElementHandler = self._handle_end
        parser.CharacterDataHandler = self._handle_data
        parser.XmlDeclHandler = self._handle_prolog
        parser.StartDoctypeDeclHandler = self._handle_doctype
        parser.StartNamespaceDeclHandler = self._handle_start_ns
        parser.EndNamespaceDeclHandler = self._handle_end_ns
        parser.ProcessingInstructionHandler = self._handle_pi
        parser.CommentHandler = self._handle_comment
        parser.DefaultHandler = self._handle_other

        # Location reporting is only support in Python >= 2.4
        if not hasattr(parser, 'CurrentLineNumber'):
            self._getpos = self._getpos_unknown

        self.expat = parser
        self._queue = []

    def __iter__(self):
        try:
            bufsize = 4 * 1024 # 4K
            done = False
            while True:
                while not done and len(self._queue) == 0:
                    data = self.source.read(bufsize)
                    if data == '': # end of data
                        if hasattr(self, 'expat'):
                            self.expat.Parse('', True)
                            del self.expat # get rid of circular references
                        done = True
                    else:
                        self.expat.Parse(data, False)
                for event in self._queue:
                    yield event
                self._queue = []
                if done:
                    break
        except expat.ExpatError, e:
            msg = str(e)
            if self.filename:
                msg += ', in ' + self.filename
            raise ParseError(msg, self.filename, e.lineno, e.offset)

    def _enqueue(self, kind, data, pos=None):
        if pos is None:
            pos = self._getpos()
        self._queue.append((kind, data, pos))

    def _getpos_unknown(self):
        return (self.filename or '<string>', -1, -1)

    def _getpos(self):
        return (self.filename or '<string>', self.expat.CurrentLineNumber,
                self.expat.CurrentColumnNumber)

    def _handle_start(self, tag, attrib):
        self._enqueue(Stream.START, (QName(tag), Attributes(attrib.items())))

    def _handle_end(self, tag):
        self._enqueue(Stream.END, QName(tag))

    def _handle_data(self, text):
        self._enqueue(Stream.TEXT, text)

    def _handle_prolog(self, version, encoding, standalone):
        self._enqueue(Stream.PROLOG, (version, encoding, standalone))

    def _handle_doctype(self, name, sysid, pubid, has_internal_subset):
        self._enqueue(Stream.DOCTYPE, (name, pubid, sysid))

    def _handle_start_ns(self, prefix, uri):
        self._enqueue(Stream.START_NS, (prefix or '', uri))

    def _handle_end_ns(self, prefix):
        self._enqueue(Stream.END_NS, prefix or '')

    def _handle_pi(self, target, data):
        self._enqueue(Stream.PI, (target, data))

    def _handle_comment(self, text):
        self._enqueue(Stream.COMMENT, text)

    def _handle_other(self, text):
        if text.startswith('&'):
            # deal with undefined entities
            try:
                text = unichr(htmlentitydefs.name2codepoint[text[1:-1]])
                self._enqueue(Stream.TEXT, text)
            except KeyError:
                lineno, offset = self._getpos()
                raise expat.error("undefined entity %s: line %d, column %d" %
                                  (text, lineno, offset))


def XML(text):
    return Stream(list(XMLParser(StringIO(text))))


class HTMLParser(html.HTMLParser, object):
    """Parser for HTML input based on the Python `HTMLParser` module.
    
    This class provides the same interface for generating stream events as
    `XMLParser`, and attempts to automatically balance tags.
    
    The parsing is initiated by iterating over the parser object:
    
    >>> parser = HTMLParser(StringIO('<UL compact><LI>Foo</UL>'))
    >>> for kind, data, pos in parser:
    ...     print kind, data
    START (u'ul', [(u'compact', u'compact')])
    START (u'li', [])
    TEXT Foo
    END li
    END ul
    """

    _EMPTY_ELEMS = frozenset(['area', 'base', 'basefont', 'br', 'col', 'frame',
                              'hr', 'img', 'input', 'isindex', 'link', 'meta',
                              'param'])

    def __init__(self, source, filename=None):
        html.HTMLParser.__init__(self)
        self.source = source
        self.filename = filename
        self._queue = []
        self._open_tags = []

    def __iter__(self):
        try:
            bufsize = 4 * 1024 # 4K
            done = False
            while True:
                while not done and len(self._queue) == 0:
                    data = self.source.read(bufsize)
                    if data == '': # end of data
                        self.close()
                        done = True
                    else:
                        self.feed(data)
                for kind, data, pos in self._queue:
                    yield kind, data, pos
                self._queue = []
                if done:
                    open_tags = self._open_tags
                    open_tags.reverse()
                    for tag in open_tags:
                        yield Stream.END, QName(tag), pos
                    break
        except html.HTMLParseError, e:
            msg = '%s: line %d, column %d' % (e.msg, e.lineno, e.offset)
            if self.filename:
                msg += ', in %s' % self.filename
            raise ParseError(msg, self.filename, e.lineno, e.offset)

    def _enqueue(self, kind, data, pos=None):
        if pos is None:
            pos = self._getpos()
        self._queue.append((kind, data, pos))

    def _getpos(self):
        lineno, column = self.getpos()
        return (self.filename, lineno, column)

    def handle_starttag(self, tag, attrib):
        fixed_attrib = []
        for name, value in attrib: # Fixup minimized attributes
            if value is None:
                value = name
            fixed_attrib.append((name, unicode(value)))

        self._enqueue(Stream.START, (QName(tag), Attributes(fixed_attrib)))
        if tag in self._EMPTY_ELEMS:
            self._enqueue(Stream.END, QName(tag))
        else:
            self._open_tags.append(tag)

    def handle_endtag(self, tag):
        if tag not in self._EMPTY_ELEMS:
            while self._open_tags:
                open_tag = self._open_tags.pop()
                if open_tag.lower() == tag.lower():
                    break
                self._enqueue(Stream.END, QName(open_tag))
            self._enqueue(Stream.END, QName(tag))

    def handle_data(self, text):
        self._enqueue(Stream.TEXT, text)

    def handle_charref(self, name):
        self._enqueue(Stream.TEXT, Markup('&#%s;' % name))

    def handle_entityref(self, name):
        self._enqueue(Stream.TEXT, Markup('&%s;' % name))

    def handle_pi(self, data):
        target, data = data.split(maxsplit=1)
        data = data.rstrip('?')
        self._enqueue(Stream.PI, (target.strip(), data.strip()))

    def handle_comment(self, text):
        self._enqueue(Stream.COMMENT, text)


def HTML(text):
    return Stream(list(HTMLParser(StringIO(text))))
