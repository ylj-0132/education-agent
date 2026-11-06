"""Start the local workbench. Opening the page does not execute tasks."""
import argparse
from pathlib import Path
from webapp.server import make_server

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('port must be 1–65535')
    server=make_server(Path(__file__).resolve().parent,args.port)
    print(f'Local workbench: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
