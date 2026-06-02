FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Instalar Helvetica Neue para subtítulos
RUN mkdir -p /usr/share/fonts/truetype/helvetica && \
    cp fonts/HelveticaNeue.ttc /usr/share/fonts/truetype/helvetica/ && \
    fc-cache -fv

CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
