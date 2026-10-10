import sys

sys.dont_write_bytecode = True
from trial_backup import main

if __name__ == "__main__":
    sys.exit(main("verify"))
