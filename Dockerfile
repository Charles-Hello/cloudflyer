FROM python:3.11-slim-bookworm as builder

# Build dummy packages to skip installing them and their dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends equivs \
    && equivs-control libgl1-mesa-dri \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: libgl1-mesa-dri\nVersion: 99.0.0\nDescription: Dummy package for libgl1-mesa-dri\n' >> libgl1-mesa-dri \
    && equivs-build libgl1-mesa-dri \
    && mv libgl1-mesa-dri_*.deb /libgl1-mesa-dri.deb \
    && equivs-control adwaita-icon-theme \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: adwaita-icon-theme\nVersion: 99.0.0\nDescription: Dummy package for adwaita-icon-theme\n' >> adwaita-icon-theme \
    && equivs-build adwaita-icon-theme \
    && mv adwaita-icon-theme_*.deb /adwaita-icon-theme.deb

FROM python:3.11-slim-bookworm

# Copy dummy packages
COPY --from=builder /*.deb /

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV CHROME_PATH=/usr/bin/chromium
ENV CHROME_BIN=/usr/bin/chromium

# Install dependencies and create cloudflyer user
# You can test Chromium running this command inside the container:
#    xvfb-run -s "-screen 0 1600x1200x24" chromium --no-sandbox
WORKDIR /app

# Install dummy packages
RUN dpkg -i /libgl1-mesa-dri.deb \
    && dpkg -i /adwaita-icon-theme.deb \
    # Install dependencies
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-common \
        xvfb \
        dumb-init \
        procps \
        curl \
        vim \
        xauth \
        wget \
        gnupg \
        ca-certificates \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libxss1 \
        libxtst6 \
        libnss3 \
        libatk-bridge2.0-0 \
        libgtk-3-0 \
        fonts-liberation \
        libappindicator3-1 \
        libu2f-udev \
        libvulkan1 \
        libdrm2 \
        xdg-utils \
        libasound2 \
        libcurl4 \
        libgbm1 \
    # Remove temporary files and hardware decoding libraries
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/lib/x86_64-linux-gnu/libmfxhw* \
    && rm -f /usr/lib/x86_64-linux-gnu/mfx/* \
    # Create cloudflyer user
    && useradd --home-dir /app --shell /bin/sh cloudflyer \
    && chown -R cloudflyer:cloudflyer .

# Download linksocks and hazetunnel
RUN wget -O /app/linksocks https://github.com/linksocks/linksocks/releases/latest/download/linksocks-linux-amd64 && \
    chmod +x /app/linksocks && \
    wget -O /app/hazetunnel https://github.com/zetxtech/hazetunnel/releases/download/v3.1.0/hazetunnel-linux-amd64 && \
    chmod +x /app/hazetunnel

# Copy application files
COPY . .

# Install Python dependencies
RUN pip install -e . \
    # Remove temporary files
    && rm -rf /root/.cache

USER cloudflyer

# Create chromium crash reports directory
RUN mkdir -p "/app/.config/chromium/Crash Reports/pending"

# Expose the port for the FastAPI server
EXPOSE 3000

# dumb-init avoids zombie chromium processes
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# Start with xvfb for virtual display
CMD ["sh", "-c", "xvfb-run -s '-screen 0 1600x1200x24' python -m cloudflyer \"$@\"", "--"]