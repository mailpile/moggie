def Main():
    import sys
    from moggie.kittens.app import AppKitten
    return AppKitten.Main(sys.argv[1:])

if __name__ == '__main__':
    Main()
