# syntax=docker/dockerfile:1
# Stages: dev (toolchain, used by .devcontainer) -> build (colcon build) -> runtime (install space only,
#         plus the dashboard bundle) -> demo (runtime + Foxglove bridge and the demo tools; compose.yaml)
#         dashboard (Node, builds the dashboard; nothing of it but the bundle reaches the robot)
ARG ROS_DISTRO=jazzy

FROM node:22.22-bookworm-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

FROM ros:${ROS_DISTRO}-ros-base AS dev
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      clang-format-18 git ninja-build python3-pip python3-pytest \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /ws/src/jarvis_ros2
# Resolve dependencies from the package manifests only, so source edits don't bust this layer.
COPY core/jarvis_core/package.xml core/jarvis_core/package.xml
COPY ros/ ros-manifests/
COPY lake/ lake-manifests/
COPY gateway/ gateway-manifests/
COPY agent/ agent-manifests/
RUN find ros-manifests lake-manifests gateway-manifests agent-manifests -mindepth 1 -not -name package.xml \
         -not -type d -delete \
    && apt-get update \
    && rosdep update --rosdistro "${ROS_DISTRO}" \
    && rosdep install --from-paths core ros-manifests lake-manifests gateway-manifests agent-manifests \
         --ignore-src -y --rosdistro "${ROS_DISTRO}" \
    && rm -rf /var/lib/apt/lists/* ros-manifests lake-manifests gateway-manifests agent-manifests

FROM dev AS build
COPY . .
RUN source "/opt/ros/${ROS_DISTRO}/setup.bash" \
    && cd /ws \
    && colcon build --merge-install --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF

FROM ros:${ROS_DISTRO}-ros-core AS runtime
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# The agent's model client (LiteLLM, hash-locked): JARVIS_LLM=0 leaves it out (heuristic analyst only).
ARG JARVIS_LLM=1
COPY --from=build /ws/src/jarvis_ros2/core/jarvis_core/package.xml /tmp/manifests/core/package.xml
COPY --from=build /ws/src/jarvis_ros2/ros /tmp/manifests/ros
COPY --from=build /ws/src/jarvis_ros2/lake /tmp/manifests/lake
COPY --from=build /ws/src/jarvis_ros2/gateway /tmp/manifests/gateway
COPY --from=build /ws/src/jarvis_ros2/agent /tmp/manifests/agent
# The Python libraries of jarvis_lake (DuckDB, Arrow, MCAP), jarvis_gateway (FastAPI, uvicorn) and
# jarvis_agent (pydantic, httpx) are not in apt: pinned in their requirements.txt.
RUN apt-get update && apt-get install -y --no-install-recommends python3-rosdep python3-pip \
    && (rosdep init || true) && rosdep update --rosdistro "${ROS_DISTRO}" \
    && rosdep install --from-paths /tmp/manifests --ignore-src -y -t exec --rosdistro "${ROS_DISTRO}" \
    && pip3 install --no-cache-dir --break-system-packages \
         -r /tmp/manifests/lake/jarvis_lake/requirements.txt \
         -r /tmp/manifests/gateway/jarvis_gateway/requirements.txt \
         -r /tmp/manifests/agent/jarvis_agent/requirements.txt \
    && if [ "${JARVIS_LLM}" = 1 ]; then \
         pip3 install --no-cache-dir --break-system-packages --ignore-installed --require-hashes \
           -r /tmp/manifests/agent/jarvis_agent/requirements-llm.txt; fi \
    && rm -rf /var/lib/apt/lists/* /tmp/manifests
COPY --from=build /ws/install /opt/jarvis
# The gateway serves it from there (no Node.js on the robot).
COPY --from=dashboard /dashboard/dist /opt/jarvis/share/jarvis_gateway/dashboard
RUN sed -i "s|^exec \"\$@\"|source /opt/jarvis/setup.bash\nexec \"\$@\"|" /ros_entrypoint.sh
ENV JARVIS_DATA=/var/lib/jarvis
VOLUME ["/var/lib/jarvis"]
CMD ["ros2", "launch", "jarvis_bringup", "jarvis.launch.py"]

FROM runtime AS demo
RUN apt-get update && apt-get install -y --no-install-recommends "ros-${ROS_DISTRO}-foxglove-bridge" \
    && rm -rf /var/lib/apt/lists/*
# `inject` and `monitor` on the PATH for `docker compose exec`, and ROS 2 sourced in interactive shells.
COPY docker/demo/bin/ /usr/local/bin/
RUN printf 'source /opt/ros/%s/setup.bash\nsource /opt/jarvis/setup.bash\n' "${ROS_DISTRO}" >> /etc/bash.bashrc
# Everything runs in this one container: keep DDS discovery inside it.
ENV ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
EXPOSE 8080 8765
CMD ["ros2", "launch", "jarvis_demo", "demo.launch.py", "foxglove:=true", "gateway_host:=0.0.0.0"]
