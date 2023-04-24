import copy
from ..util.dumbcode import to_json


class _dict_helper(dict):
    UNSET = '_UNSET'
    ATTRS = {}

    def __init__(self, *args, **kwargs):
        validate = kwargs.get('_validate')
        for _a in [a for a in kwargs if a[:1] == '_']:
            if _a in kwargs:
                del kwargs[_a]

        super().__init__(*args, **kwargs)

        def create_property(a, _type, _path):
            getter = lambda s, **kw: s._get_custom_attr(_type, _path, **kw)
            setter = lambda s, v: s._set_custom_attr(_type, _path, v)
            setattr(self.__class__, a, property(getter, setter))
            return getter, setter

        attrs = self.default_attrs()
        for attr in attrs:
            prop_type = attrs[attr][0]
            prop_path = attrs[attr][-1].split('/')
            getter, setter = create_property(attr, prop_type, prop_path)
            if len(attrs[attr]) > 2:
                if getter(self, default=None) is None:
                    if validate:
                        raise ValueError('Missing required value: %s' % attr)
                    setter(self, self.ATTRS[attr][1])

    def default_attrs(self):
        return self.ATTRS

    def _get_custom_attr(self, prop_type, prop_path, default=UNSET):
        d = self
        try:
            for p in prop_path[:-1]:
                d = d[p]
            return prop_type(d[prop_path[-1]])
        except KeyError as e:
            if default is not self.UNSET:
                return default
            raise AttributeError(e)

    def _set_custom_attr(self, prop_type, prop_path, value):
        d = self
        for p in prop_path[:-1]:
            if p not in d:
                d[p] = {}
            d = d[p]
        d[prop_path[-1]] = prop_type(value)

    def __str__(self):
        return to_json(self)
