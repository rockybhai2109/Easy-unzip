# Use a suitable base image. Python 3.13 is used here as per your logs.
FROM python:3.13-slim
# Install unrar and other necessary system dependencies.
# apt-get update is crucial before installing new packages.
RUN apt-get update && apt-get install -y unrar

# Set the working directory inside the container.
WORKDIR /app

# Copy your requirements.txt file and install Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container.
COPY . .

# Set the PORT environment variable. Render injects a port to listen on.
# Your ultimate_unzipper.py script already uses this.
ENV PORT 10000
EXPOSE ${PORT}

# Define the command to run your application when the container starts.
# The -u flag is for unbuffered output, which can be helpful in logs.
CMD ["python", "-u", "ultimate_unzipper.py"]