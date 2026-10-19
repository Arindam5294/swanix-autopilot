import sys; from notify import telegram
sys.exit(0 if telegram(" ".join(sys.argv[1:])) else 1)
