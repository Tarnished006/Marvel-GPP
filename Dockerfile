# Start with an ARM64 Ubuntu 22.04 base image
FROM ubuntu:22.04

# Avoid user interaction during apt installations
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies for adding PPAs, then add Python 3.11
RUN apt-get update && apt-get install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    curl \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as the default 'python3'
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Install pip explicitly for Python 3.11
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Set working directory
WORKDIR /app

# Copy your requirements and install them
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Copy the Aegis-Touch source code into the container
COPY . /app

# Command to run your Qt/Vision pipeline
CMD ["python3", "main.py"]