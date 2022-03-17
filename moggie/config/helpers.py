from configparser import ConfigParser, NoOptionError, _UNSET


class ListItemProxy(list):
    def __init__(self, ac, section, item):
        super().__init__()
        self._config = ac
        self._key = section
        self._item = item
        try:
            items = ac.get(section, item).split(',')
            self.extend(i.strip() for i in items)
        except (TypeError, AttributeError, KeyError, NoOptionError):
            pass

    config = property(lambda s: s._config)
    config_key = property(lambda s: s._key)

    def _write_back(self):
        if self._config is not None:
            list_str = ', '.join(str(i) for i in self)
            self._config.set(self._key, self._item, list_str)

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
        self._config = ac
        self._key = section
        self._item = item
        try:
            pairs = ac.get(section, item).split(',')
            self.update(dict(pair.strip().split(':', 1) for pair in pairs))
        except (TypeError, AttributeError, KeyError, ValueError, NoOptionError):
            pass

    config_key = property(lambda s: s._key)

    def _write_back(self):
        if self._config is not None:
            dict_str = ', '.join('%s:%s' % (k, v) for k, v in self.items())
            self._config.set(self._key, self._item, dict_str)

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
        self._config = ac
        self._key = section

    config = property(lambda s: s._config)
    config_key = property(lambda s: s._key)

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
        return dict(
            (key, self.__getattr__(key))
            for key in self._KEYS if key in self)
