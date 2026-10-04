FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0
RUN python -m pip install --no-cache-dir playwright==1.63.0 pyee==13.0.1 greenlet==3.5.6 typing-extensions==4.16.0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 1000:1000
ENTRYPOINT ["python", "/suite/acceptance.py"]
