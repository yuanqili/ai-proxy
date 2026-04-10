"""Run: python -m aiproxy"""

import uvicorn

from aiproxy.settings import settings


def main() -> None:
    uvicorn.run(
        "aiproxy.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
