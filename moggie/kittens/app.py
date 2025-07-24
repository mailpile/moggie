# Migration to kittens!
#
# FIXME:
#   * init_server which instanciates our core.app
#   * custom access control method using our tokens
#   * pluggable CLI methods
#   * muttalike mode
#   * triage which things run locally and which require a backend.
#
from .common import MoggieKitten


class AppKitten(MoggieKitten):
    """moggie

    Welcome to Moggie!

    """
    class Configuration(MoggieKitten.Configuration):
        WORKER_NAME = 'app'

    # FIXME: 

    @classmethod
    def Setup(cls):
        if not hasattr(cls, 'api_default'):
            from ..app.cli import CLI_COMMANDS
            for name, val in CLI_COMMANDS.items():
                print('Add plumbing for %s' % name)

    @classmethod
    def Main(cls, args):
        cls.Setup()
        return super().Main(args)
