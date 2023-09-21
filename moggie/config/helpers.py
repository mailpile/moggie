import re
from configparser import ConfigParser, NoOptionError, _UNSET

from ..util.dumbcode import *


def cfg_bool(val):
    if isinstance(val, str):
        val = val.lower()
    if val in (False, 'false', 'n', 'no',  0, '0'):
        return False
    if val in (True,  'true',  'y', 'yes', 1, '1'):
        return True
    return None


class ListItemProxy(list):
    def __init__(self, ac, section, item, delim=','):
        super().__init__()
        self._config = ac
        self._key = section
        self._item = item
        self.delim = delim
        self.access_denied = False
        self.no_write_back = None
        try:
            data = ac.get(section, item, permerror=True).strip()
            if data:
                items = [self._decode(i) for i in data.split(self.delim)]
                super().extend(i for i in items if i)
        except PermissionError as e:
            self.access_denied = e
        except (TypeError, AttributeError, KeyError, NoOptionError) as e:
            pass

    config = property(lambda s: s._config)
    config_key = property(lambda s: s._key)

    def __enter__(self):
        self.no_write_back = 0

    def __exit__(self, *args, **kwargs):
        changed, self.no_write_back = self.no_write_back, None
        if changed:
            self._write_back()

    def _decode(self, val):
        return val.strip()

    def __repr__(self):
        data = '(encrypted)' if self.access_denied else super().__repr__()
        return '<ListItemProxy(%s/%s)=%s>' % (self._key, self._item, data)

    def _write_back(self):
        if self.no_write_back is not None:
            self.no_write_back += 1
            return
        list_str = (self.delim+' ').join(str(i) for i in self)
        if len(list_str) > 60:
            list_str = list_str.replace(self.delim, self.delim+'\n')
        self._config.set(self._key, self._item, list_str)

    def _validate(self, val):
        val = str(val)
        if self.delim in val:
            raise ValueError('Illegal character in value')
        return val

    def __iadd__(self, val):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().__iadd__(val)
        self._write_back()

    def __setitem__(self, key, val):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().__setitem__(key, self._validate(val))
        self._write_back()

    def append(self, val):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().append(self._validate(val))
        self._write_back()
        return self

    def extend(self, val):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        extending = [self._validate(v) for v in val]
        if extending:
            super().extend(extending)
            self._write_back()
        return self

    def pop(self, pos):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        rv = super().pop(pos)
        self._write_back()
        return rv

    def remove(self, item):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().remove(item)
        self._write_back()

    def clear(self):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().clear()
        self._write_back()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self._write_back()


class EncodingListItemProxy(ListItemProxy):
    CLEAR_OK = re.compile(r'^[a-zA-Z0-9 @\\/\.,:;_|\-=]*$')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.esc_delim = ''.join('%%%2.2X' % ord(c) for c in self.delim)

    def _decode(self, val):
        if val[:1] == '=':
            return dumb_decode(val[1:])
        return val.strip()

    def _validate(self, val):
        def rdelim(t):
            return t.replace(self.delim, self.esc_delim)
        if isinstance(val, bytes):
            try:
                val = str(val, 'utf-8')
            except:
                return rdelim('=' + dumb_encode_asc(val))
        if ((val[:1] == '=')
                or (self.delim in val)
                or not self.CLEAR_OK.match(val)):
            val = rdelim('=' + dumb_encode_asc(val))
        return val


class DictItemProxy(dict):
    def __init__(self, ac, section, item):
        super().__init__()
        self._config = ac
        self._key = section
        self._item = item
        self.access_denied = False
        try:
            pairs = ac.get(section, item, permerror=True).split(',')
            super().update(dict(pair.strip().split(':', 1) for pair in pairs))
        except PermissionError as e:
            self.access_denied = e
        except (TypeError, AttributeError, KeyError, ValueError, NoOptionError):
            pass

    def __repr__(self):
        data = '(encrypted)' if self.access_denied else super().__repr__()
        return '<DictItemProxy(%s/%s)=%s>' % (self._key, self._item, data)

    config_key = property(lambda s: s._key)

    def _write_back(self):
        dict_str = ', '.join('%s:%s' % (k, v) for k, v in self.items())
        if len(dict_str) > 60:
            dict_str = dict_str.replace(', ', ',\n ')
        self._config.set(self._key, self._item, dict_str)

    def _validate(self, key, val):
        val = str(val)
        if ':' in key or ',' in key:
            raise KeyError('Illegal character in key')
        if ',' in val:
            raise ValueError('Illegal character in value')
        return val

    def __setitem__(self, key, val):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().__setitem__(key, self._validate(key, val))
        self._write_back()

    def __delitem__(self, key):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().__delitem__(key)
        self._write_back()

    def clear(self):
        if self.access_denied:
            raise PermissionError(self.access_denied)
        super().clear()
        self._write_back()


class ConfigSectionProxy:
    _KEYS = {}
    _EXTRA_KEYS = []

    def __init__(self, ac, section):
        self._config = ac
        self._key = section

    config = property(lambda s: s._config)
    config_key = property(lambda s: s._key)

    def __repr__(self):
        return ('<ConfigSectionProxy(%s)=%s>'
            % (self._key, self.as_dict()))

    def __contains__(self, attr):
        return (attr in self._config[self._key])

    def __getattr__(self, attr):
        if attr[:1] == '_':
            return object.__getattribute__(self, attr)
        if attr in self._KEYS:
            try:
                return self._KEYS[attr](self._config.get(self._key, attr))
            except NoOptionError:
                return None
        else:
            return object.__getattribute__(self, attr)

    def __setattr__(self, attr, val):
        if attr[:1] == '_':
            return object.__setattr__(self, attr, val)
        if attr in self._KEYS:
            if val is not None:
                val = str(val)
            return self._config.set(self._key, attr, val)
        raise KeyError(attr)

    def magic_test(self):
        return 'magic'

    def as_dict(self):
        keys = sorted(self._EXTRA_KEYS + list(self._KEYS.keys()))
        return dict(
            (key, self.__getattr__(key))
            for key in keys if key in self)
