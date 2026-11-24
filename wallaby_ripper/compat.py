"""The small pathlib additions absent in Ubuntu 14.04's Python 3.4."""
import errno
import io
import os
import pathlib


class Path(type(pathlib.Path())):
    @classmethod
    def home(cls):
        return cls(os.path.expanduser('~'))

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        try:
            if parents:
                os.makedirs(str(self), mode=mode)
            else:
                os.mkdir(str(self), mode)
        except OSError as error:
            if not (exist_ok and error.errno == errno.EEXIST and self.is_dir()):
                raise

    def read_text(self, encoding='utf-8'):
        with io.open(str(self), 'r', encoding=encoding) as stream:
            return stream.read()

    def write_text(self, data, encoding='utf-8'):
        with io.open(str(self), 'w', encoding=encoding) as stream:
            return stream.write(data)

    def read_bytes(self):
        with open(str(self), 'rb') as stream:
            return stream.read()

    def write_bytes(self, data):
        with open(str(self), 'wb') as stream:
            return stream.write(data)

    def expanduser(self):
        return Path(os.path.expanduser(str(self)))
