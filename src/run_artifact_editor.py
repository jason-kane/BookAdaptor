#!/usr/bin/env python3
from artifact_editor import app

if __name__ == "__main__":
    try:
        app.run()
    except Exception as e:
        print(f"An error occurred: {e}")
        raise

    print('app.run() completed')
else:
    print(__name__)