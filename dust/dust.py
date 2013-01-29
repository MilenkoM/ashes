from __future__ import unicode_literals

import re
import cgi
import urllib
from pprint import pprint
# need to add group for literals
# switch to using word boundary for params section
node_re = re.compile(r'({'
                     r'(?P<closing>\/)?'
                     r'(?P<symbol>[\~\#\?\@\:\<\>\+\^])?'
                     r'(?P<refpath>[a-zA-Z0-9_\$\.]+|"[^"]+")'
                     r'(?:\:(?P<contpath>[a-zA-Z0-9\$\.]+))?'
                     r'(?P<filters>\|[a-z]+)*?'
                     r'(?P<params> \w+\=(("[^"]*?")|([\w\.]+)))*?'
                     r'(?P<selfclosing>\/)?'
                     r'\})',
                     flags=re.MULTILINE)

key_re_str = '[a-zA-Z_$][0-9a-zA-Z_$]*'
key_re = re.compile(key_re_str)
path_re = re.compile(key_re_str + '?(\.' + key_re_str + ')+')

#comment_re = ''  # TODO
def strip_comments(text):
    return re.sub(r'\{!.+?!\}', '', text, flags=re.DOTALL).strip()


def get_path_or_key(pork):
    if pork == '.':
        pk = ('path', True, [])
    elif path_re.match(pork):
        f_local = pork.startswith('.')
        pk = ('path', f_local, pork.split('.'))
    elif key_re.match(pork):
        pk = ('key', pork)
    else:
        raise ValueError('expected a path or key, not %r' % pork)
    return pk


def params_to_kv(params_str):
    ret = []
    new_k, v = None, None
    k, _, tail = params_str.partition('=')
    while tail:
        tmp, _, tail = tail.partition('=')
        if not tail:
            v = tmp
        else:
            v, new_k = tmp.split()
        ret.append((k.strip(), v.strip()))
        k = new_k
    return ret


def wrap_params(param_kv):
    ret = []
    for k, v in param_kv:
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
            v_tuple = ('literal', v)
        else:
            v_tuple = get_key_or_path(v)
        ret.append(('param', ('literal', k), v_tuple))
    return ret


ALL_ATTRS = ('closing', 'symbol', 'refpath', 'contpath',
             'filters', 'params', 'selfclosing')


class Tag(object):
    req_attrs = ()
    ill_attrs = ()

    def __init__(self, **kw):
        self.match_str = kw.pop('match_str')
        self._attr_dict = kw
        self.set_attrs(kw)

    @property
    def param_list(self):
        try:
            return params_to_kv(self.params)
        except AttributeError:
            return []

    def set_attrs(self, attr_dict, raise_exc=True):
        cn = self.__class__.__name__
        all_attrs = getattr(self, 'all_attrs', ())
        if all_attrs:
            req_attrs = [a for a in ALL_ATTRS if a in all_attrs]
            ill_attrs = [a for a in ALL_ATTRS if a not in all_attrs]
        else:
            req_attrs = getattr(self, 'req_attrs', ())
            ill_attrs = getattr(self, 'ill_attrs', ())

        opt_attrs = getattr(self, 'opt_attrs', ())
        if opt_attrs:
            ill_attrs = [a for a in ill_attrs if a not in opt_attrs]
        for attr in req_attrs:
            if attr_dict.get(attr, None) is None:
                raise ValueError('%s expected %s' % (cn, attr))
        for attr in ill_attrs:
            if attr_dict.get(attr, None) is not None:
                raise ValueError('%s does not take %s' % (cn, attr))

        avail_attrs = [a for a in ALL_ATTRS if a not in ill_attrs]
        for attr in avail_attrs:
            setattr(self, attr, attr_dict.get(attr, ''))
        return True

    def __repr__(self):
        cn = self.__class__.__name__
        return '%s(%r)' % (cn, self.match_str)

    @classmethod
    def from_match(cls, match):
        kw = dict([(k, v) for k, v in match.groupdict().items()
                  if v is not None])
        kw['match_str'] = match.group(0)
        obj = cls(**kw)
        obj.orig_match = match
        return obj


class BlockTag(Tag):
    all_attrs = ('contpath',)


class ReferenceTag(Tag):
    all_attrs = ('refpath',)
    opt_attrs = ('filters',)

    def to_dust_ast(self):
        pork = get_path_or_key(self.refpath)
        filters = ['filters']
        if self.filters:
            f_list = self.filters.split('|')[1:]
            for f in f_list:
                filters.append(f)
        return [['reference', pork, filters]]


class SectionTag(Tag):
    ill_attrs = ('closing')


class ClosingTag(Tag):
    all_attrs = ('closing', 'refpath')


class SpecialTag(Tag):
    all_attrs = ('symbol', 'refpath')

    def to_dust_ast(self):
        return [['special', self.refpath]]


class BlockTag(Tag):
    all_attrs = ('symbol', 'refpath')


class PartialTag(Tag):
    req_attrs = ('symbol', 'refpath', 'selfclosing')

    def to_dust_ast(self):
        context = ['context']
        contpath = self.contpath
        if contpath:
            context.append(get_path_or_key(contpath))
        return [['partial',
                 ['literal', self.refpath],
                 context]]

def get_tag(match):
    groups = match.groupdict()
    symbol = groups['symbol']
    closing = groups['closing']
    refpath = groups['refpath']
    if closing:
        tag_type = ClosingTag
    elif symbol is None and refpath is not None:
        tag_type = ReferenceTag
    elif symbol in '#?^<+@%':
        tag_type = SectionTag
    elif symbol == '~':
        tag_type = SpecialTag
    elif symbol == ':':
        tag_type = BlockTag
    elif symbol == '>':
        tag_type = PartialTag
    else:
        raise ValueError('invalid tag')
    return tag_type.from_match(match)


class WhitespaceToken(object):
    def __init__(self, ws=''):
        self.ws = ws

    def __repr__(self):
        disp = self.ws
        if len(disp) > 13:
            disp = disp[:10] + '...'
        return u'WhitespaceToken(%r)' % disp


def split_leading(text):
    leading_stripped = text.lstrip()
    leading_ws = text[:len(text) - len(leading_stripped)]
    return leading_ws, leading_stripped


class BufferToken(object):
    def __init__(self, text=''):
        self.text = text
        #if 'not bar!' in self.text:
        #    import pdb;pdb.set_trace()

    def __repr__(self):
        disp = self.text
        if len(self.text) > 30:
            disp = disp[:27] + '...'
        return u'BufferToken(%r)' % disp

    def to_dust_ast(self):
        # It is hard to simulate the PEG parsing in this case,
        # especially while supporting universal newlines.
        if not self.text:
            return []
        rev = []
        remaining_lines = self.text.splitlines()
        if self.text[-1] in ('\n', '\r'):
            # kind of a bug in splitlines if you ask me.
            remaining_lines.append('')
        while remaining_lines:
            line = remaining_lines.pop()
            leading_ws, lstripped = split_leading(line)
            if remaining_lines:
                if lstripped:
                    rev.append(('buffer', lstripped))
                rev.append(('format', '\n', leading_ws))
            else:
                if line:
                    rev.append(('buffer', line))
        ret = list(reversed(rev))
        return ret


def tokenize(source):
    # TODO: line/column numbers
    # removing comments
    source = strip_comments(source)
    tokens = []
    prev_end = 0
    start = None
    end = None
    for match in node_re.finditer(source):
        start, end = match.start(1), match.end(1)
        if prev_end < start:
            tokens.append(BufferToken(source[prev_end:start]))
        prev_end = end
        tag = get_tag(match)
        tokens.append(tag)
    tail = source[prev_end:]
    if tail:
        tokens.append(BufferToken(tail))
    return tokens


#########
# PARSING
#########

class Section(object):
    def __init__(self, start_tag=None, blocks=None):
        if start_tag is None:
            name = '<root>'
        else:
            name = start_tag.refpath
        self.name = name
        self.start_tag = start_tag
        self.blocks = blocks or []

    def add(self, obj):
        if type(obj) == Block:
            self.blocks.append(obj)
        else:
            if not self.blocks:
                self.blocks = [Block()]
            self.blocks[-1].add(obj)

    def to_dict(self):
        ret = {self.name: dict([(b.name, b.to_list()) for b in self.blocks])}
        return ret

    def to_dust_ast(self):
        symbol = self.start_tag.symbol
        key = self.start_tag.refpath

        context = ['context']
        contpath = self.start_tag.contpath
        if contpath:
            context.append(get_path_or_key(contpath))

        params = ['params']
        param_list = self.start_tag.param_list
        if param_list:
            params.extend(wrap_params(param_list))

        bodies = ['bodies']
        if self.blocks:
            #body_list = []
            for b in reversed(self.blocks):
                bodies.extend(b.to_dust_ast())
            #bodies.append(body_list)

        return [[symbol,
                [u'key', key],
                context,
                params,
                bodies]]


class Block(object):
    def __init__(self, name='block'):
        if not name:
            raise ValueError('blocks need a name, not: %r' % name)
        self.name = name
        self.items = []

    def add(self, item):
        self.items.append(item)

    def to_list(self):
        ret = []
        for i in self.items:
            try:
                ret.append(i.to_dict())
            except AttributeError:
                ret.append(i)
        return ret

    def _get_dust_body(self):
        # for usage by root block in ParseTree
        ret = []
        for i in self.items:
            ret.extend(i.to_dust_ast())
        return ret

    def to_dust_ast(self):
        name = self.name
        body = ['body']
        dust_body = self._get_dust_body()
        if dust_body:
            body.extend(dust_body)
        return [['param',
                ['literal', name],
                body]]


class ParseTree(object):
    def __init__(self, root_block):
        self.root_block = root_block

    def to_dust_ast(self):
        ret = ['body']
        ret.extend(self.root_block._get_dust_body())
        return ret

    @classmethod
    def from_tokens(cls, tokens):
        root_sect = Section()
        ss = [root_sect]  # section stack
        for token in tokens:
            if type(token) == SectionTag:
                new_s = Section(token)
                ss[-1].add(new_s)
                if not token.selfclosing:
                    ss.append(new_s)
            elif type(token) == ClosingTag:
                if len(ss) <= 1:
                    raise ValueError('closing tag before opening tag: %r' % token)
                if token.refpath != ss[-1].start_tag.refpath:
                    raise ValueError('nesting error')
                ss.pop()
            elif type(token) == BlockTag:
                if len(ss) <= 1:
                    raise ValueError('cannot start blocks outside of a section')
                new_b = Block(name=token.refpath)
                ss[-1].add(new_b)
            else:
                ss[-1].add(token)
        return cls(root_sect.blocks[0])

    @classmethod
    def from_source(cls, src):
        tokens = tokenize(src)
        return cls.from_tokens(tokens)

#########
# Runtime
#########
def escape_html(text):
    text = unicode(text)
    return cgi.escape(text, True).replace("'", '&squot;')


def escape_js(text):
    text = unicode(text)
    return (text
            .replace('\\', '\\\\')
            .replace('"', '\\"')
            .replace("'", "\\'")
            .replace('\r', '\\r')
            .replace('\u2028', '\\u2028')
            .replace('\u2029', '\\u2029')
            .replace('\n', '\\n')
            .replace('\f', '\\f')
            .replace('\t', '\\t'))


def escape_uri(text):
    return urllib.quote(text)


def escape_uri_component(text):
    return (escape_uri(text)
            .replace('/', '%2F')
            .replace('?', '%3F')
            .replace('=', '%3D')
            .replace('&', '%26'))


class Template(object):
    pass


class DustEnv(object):
    default_filters = {
        'h': escape_html,
        'j': escape_js,
        'u': escape_uri,
        'uc': escape_uri_component}

    def __init__(self):
        self.templates = {}
        self.filters = dict(self.default_filters)

    def compile(self, string, name, src_file=None):
        pass

    def load(self, src_file, name=None):
        if name is None:
            name = src_file
        if src_file in self.templates:
            return self.templates[name].root_node
        else:
            with open(src_file, 'r') as f:
                code = f.read()
            return self.compile(code, name, src_file=src_file)

    def render(self, name, model):
        try:
            template = self.templates[name]
        except KeyError:
            raise ValueError('No template named "%s"' % name)

        return template.render(model, env=self)


#dust = default_env = DustEnv()
