# Use an official Python runtime as a parent image
FROM python:3.10-slim-bullseye

# Set the working directory in the container
WORKDIR /app

# Install ffmpeg and other necessary packages
# apt-get update updates the package list
# apt-get install -y installs ffmpeg and git (if needed for yt-dlp)
# rm -rf /var/lib/apt/lists/* cleans up apt cache to reduce image size
    RUN apt-get update && apt-get install -y ffmpeg git build-essential gcc python3-dev && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 80 available to the world outside this container
# EXPOSE 80

# Run bot.py when the container launches
# CMD ["python3", "bot.py"]
# Using the Procfile entry point for Railway
CMD ["python3", "bot.py"]

ontainer launches
# CMD ["python3", "bot.py"]
# Using the Procfile entry point for Railway
CMD ["python3", "bot.py"]

