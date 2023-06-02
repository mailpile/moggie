# Fancy exceptions we can serialize/deserialize over our APIs
import traceback


class APIException(Exception):
    def __init__(self, *args, **data):
        from_dict = data.get('_from', {})
        if hasattr(from_dict, 'as_dict'):
            from_dict = from_dict.as_dict()

        self.exc_args = from_dict.get('exc_args', args)
        self.exc_data = from_dict.get('exc_data', data)
        self.traceback = from_dict.get('traceback')

        for cleanup in ('_from', ):
            if cleanup in data:
                del data[cleanup]
        super().__init__(*self.exc_args)
        self.validate()

    def validate(self):
        pass

    def as_dict(self):
        if self.traceback is None:
            self.traceback = traceback.format_exc()
        return {
            'exception': self.__class__.__name__,
            'exc_args': self.exc_args,
            'exc_data': self.exc_data,
            'traceback': self.traceback}


class APIAccessDenied(APIException):
    pass


class NeedInfoException(APIException):
    class Need(dict):
        def __init__(self, label, field, datatype='text'):
            super().__init__({
                'field': field, 'datatype': datatype, 'label': label})

        label = property(lambda s: s['label'], lambda s,v: s.__setitem__('label', v))
        field = property(lambda s: s['field'], lambda s,v: s.__setitem__('field', v))
        datatype = property(lambda s: s['datatype'], lambda s,v: s.__setitem__('datatype', v))

    def validate(self):
        self.need = self.exc_data.get('need')
        if not isinstance(self.need, list):
            raise TypeError('NeedInfoException: Invalid arguments')
        for i, d in enumerate(self.need):
            if isinstance(d, NeedInfoException.Need):
                pass
            elif isinstance(d, dict):
                self.need[i] = NeedInfoException.Need(
                    d['label'], d['field'], d['datatype'])
            else:
                raise TypeError('NeedInfoException: Invalid arguments')


def reraise(as_dict):
    raise {
            'NeedInfoException': NeedInfoException
        }.get(as_dict['exception'], APIException)(_from=as_dict)


if __name__ == '__main__':
    import json

    e = APIException('wtf')
    e2 = APIException(_from=e)
    assert(e.as_dict() == e2.as_dict())

    need = NeedInfoException.Need
    nie = NeedInfoException('Need more info!', need=[
        need('Username', 'username'),
        need('Password', 'password', datatype='password'),
        ])
    assert(nie.need[0].label == 'Username')
    assert(nie.need[0].datatype == 'text')
    assert(nie.need[1].datatype == 'password')

    nie2 = NeedInfoException(_from=nie)
    assert(nie.as_dict() == nie2.as_dict())

    try:
        nie3 = NeedInfoException('Okay', {})
        assert(not 'reached')
    except TypeError:
        pass

    try:
        reraise(json.loads(json.dumps(nie.as_dict())))
        assert(not 'reached')
    except NeedInfoException as nie4:
        assert(nie4.need[0].label == 'Username')
        assert(nie4.need[1].datatype == 'password')
   
    print('Tests passed OK')
