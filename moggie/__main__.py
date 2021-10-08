import sys
from .app import Main

if __name__ == "__main__":
    try:
        Main(sys.argv[1:])
    except KeyboardInterrupt:
        pass
