# syntax=docker/dockerfile:1
# Stages: dev (toolchain, used by .devcontainer) -> build (colcon build) -> runtime (install space only)
ARG ROS_DISTRO=jazzy

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
RUN find ros-manifests -mindepth 1 -not -name package.xml -not -type d -delete \
    && apt-get update \
    && rosdep update --rosdistro "${ROS_DISTRO}" \
    && rosdep install --from-paths core ros-manifests --ignore-src -y --rosdistro "${ROS_DISTRO}" \
    && rm -rf /var/lib/apt/lists/* ros-manifests

FROM dev AS build
COPY . .
RUN source "/opt/ros/${ROS_DISTRO}/setup.bash" \
    && cd /ws \
    && colcon build --merge-install --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF

FROM ros:${ROS_DISTRO}-ros-core AS runtime
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY --from=build /ws/src/jarvis_ros2/core/jarvis_core/package.xml /tmp/manifests/core/package.xml
COPY --from=build /ws/src/jarvis_ros2/ros /tmp/manifests/ros
RUN apt-get update && apt-get install -y --no-install-recommends python3-rosdep \
    && (rosdep init || true) && rosdep update --rosdistro "${ROS_DISTRO}" \
    && rosdep install --from-paths /tmp/manifests --ignore-src -y -t exec --rosdistro "${ROS_DISTRO}" \
    && rm -rf /var/lib/apt/lists/* /tmp/manifests
COPY --from=build /ws/install /opt/jarvis
RUN sed -i "s|^exec \"\$@\"|source /opt/jarvis/setup.bash\nexec \"\$@\"|" /ros_entrypoint.sh
ENV JARVIS_DATA=/var/lib/jarvis
VOLUME ["/var/lib/jarvis"]
CMD ["ros2", "launch", "jarvis_bringup", "jarvis.launch.py"]
