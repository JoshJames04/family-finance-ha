FROM python:3.13-slim
WORKDIR /stub
RUN pip install --no-cache-dir fastapi "uvicorn[standard]"
COPY stub.py .
CMD ["uvicorn", "stub:app", "--host", "0.0.0.0", "--port", "8123"]
