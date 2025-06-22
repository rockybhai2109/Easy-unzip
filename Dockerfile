# Use a suitable base image. Python 3.13 is used here.
FROM python:3.13-slim

# Create a new sources list file to enable contrib and non-free repositories
# for Debian Bookworm (which python:3.13-slim is based on).
RUN echo "deb http://deb.debian.org/debian bookworm main contrib non-free" > /etc/apt/sources.list.d/debian.list && \
    echo "deb http://deb.debian.org/debian bookworm-updates main contrib non-free" >> /etc/apt/sources.list.d/debian.list && \
    echo "deb http://deb.debian.org/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list.d/debian.list && \
    # Now update apt lists with the new sources
    apt-get update && \
    # Install unrar
    apt-get install -y unrar && \
    # Clean up apt cache to keep the image size small
    rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container.
WORKDIR /app

# Copy your requirements.txt file and install Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container.
COPY . .

# Set the PORT environment variable. Render injects a port to listen on.
ENV PORT 10000
EXPOSE ${PORT}

# Define the command to run your application when the container starts.
CMD ["python", "-u", "ultimate_unzipper.py"]