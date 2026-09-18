import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.deploy_bots import deploy_bots

if __name__ == "__main__":
    deploy_bots(network="testnet")
