import logging

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(
        "android_publisher.api:create_app",
        factory=True,
        host="127.0.0.1",
        port=8080,
        workers=1,
        access_log=False,
    )
