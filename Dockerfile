# Dockerfile for the MyGroceries Bot
# 1. Use an official Python runtime as a parent image
FROM python:3.12-slim-bookworm

# 2. Install system dependencies required for audio processing
RUN apt-get update && apt-get install -y ffmpeg

# 3. Set the working directory inside the container
WORKDIR /app

# 4. Upgrade pip and install essential build tools first
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# 5. Copy the requirements file into the container
COPY requirements.txt .

# 6. Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 7. Copy the rest of your application's code into the container
COPY . .

# 8. Make the startup script executable
RUN chmod +x start.sh

# 9. Specify the command to run the startup script
CMD ["./start.sh"]