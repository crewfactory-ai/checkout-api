FROM docker.io/library/python:3.14.10-slim-trixie
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile -r requirements.txt
COPY checkout/ checkout/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "checkout.app"]
