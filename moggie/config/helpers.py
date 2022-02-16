from configparser import ConfigParser, NoOptionError, _UNSET


class ListItemProxy(list):
    def __init__(self, ac, section, item):
        super().__init__()
        self._ac = ac
        self._section = section
        self._item = item
        try:
            items = ac.get(section, item).split(',')
            self.extend(i.strip() for i in items)
        except (TypeError, AttributeError, KeyError, NoOptionError):
            pass

    config_section = property(lambda s: s._section)

    def _write_back(self):
        if self._ac is not None:
            list_str = ', '.join(str(i) for i in self)
            self._ac.set(self._section, self._item, list_str)

    def _validate(self, val):
        val = str(val)
        if ',' in val:
            raise ValueError('Illegal character in value')
        return val

    def __iadd__(self, val):
        super().__iadd__(val)
        self._write_back()

    def __setitem__(self, key, val):
        super().__setitem__(key, self._validate(val))
        self._write_back()

    def append(self, val):
        super().append(self._validate(val))
        self._write_back()

    def extend(self, val):
        super().extend(self._validate(v) for v in val)
        self._write_back()

    def clear(self):
        super().clear()
        self._write_back()


class DictItemProxy(dict):
    def __init__(self, ac, section, item):
        super().__init__()
        self._ac = ac
        self._section = section
        self._item = item
        try:
            pairs = ac.get(section, item).split(',')
            self.update(dict(pair.strip().split(':', 1) for pair in pairs))
        except (TypeError, AttributeError, KeyError, ValueError, NoOptionError):
            pass

    config_section = property(lambda s: s._section)

    def _write_back(self):
        if self._ac is not None:
            dict_str = ', '.join('%s:%s' % (k, v) for k, v in self.items())
            self._ac.set(self._section, self._item, dict_str)

    def _validate(self, key, val):
        val = str(val)
        if ':' in key or ',' in key:
            raise KeyError('Illegal character in key')
        if ',' in val:
            raise ValueError('Illegal character in value')
        return val

    def __setitem__(self, key, val):
        super().__setitem__(key, self._validate(key, val))
        self._write_back()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._write_back()

    def clear(self):
        super().clear()
        self._write_back()


class ConfigSectionProxy:
    _KEYS = {}

    def __init__(self, ac, section):
        self._ac = ac
        self._section = section

    config_section = property(lambda s: s._section)

    def __contains__(self, attr):
        return (attr in self._ac[self._section])

    def __getattr__(self, attr):
        if attr[:1] == '_':
            return object.__getattribute__(self, attr)
        if attr in self._KEYS:
            try:
                return self._KEYS[attr](self._ac.get(self._section, attr))
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
            return self._ac.set(self._section, attr, val)
        raise KeyError(attr)

    def magic_test(self):
        return 'magic'
