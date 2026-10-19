# Releasing Jarvis

Every package of the repository is released together, with one version
([semver](https://semver.org): a breaking change to the interfaces, schemas, languages or API is
a new major version). A release is: the versions and changelogs on `main`, a tag, then the
release workflow builds and publishes the rest.

## 1. Prepare (a pull request)

```bash
git checkout -b release-X.Y.Z origin/main
python3 scripts/version.py X.Y.Z      # every package.xml, setup.py, __version__, CMake, dashboard, panel;
                                      # and "Forthcoming" -> "X.Y.Z (today)" in every changelog
git diff                              # versions and changelog headings only
```

- Read every changelog section that just got its date: [CHANGELOG.md](CHANGELOG.md) (the
  release notes) and each package's `CHANGELOG.rst` (what bloom publishes). `catkin_generate_changelog`
  drafts entries from the commits since the last tag if a package's section is missing some.
- Open the pull request, let CI go green, merge it.

## 2. Tag (publishes)

```bash
git checkout main && git pull
git tag -a vX.Y.Z -m "Jarvis X.Y.Z"
git push origin vX.Y.Z
```

The [release workflow](.github/workflows/release.yml) then:

1. checks that the tag is the packages' version and that CHANGELOG.md has its section;
2. builds and pushes the Docker images, with an SBOM and build provenance:
   - `ghcr.io/<owner>/jarvis:X.Y.Z` and `:X.Y`: Jarvis (`docker/Dockerfile`, target `runtime`);
   - `ghcr.io/<owner>/jarvis-demo:X.Y.Z` and `:X.Y`: with the demo robot (target `demo`);
3. creates the GitHub release, its notes taken from CHANGELOG.md, with the Foxglove panel
   (`guilyx.jarvis-panel-X.Y.Z.foxe`, built and tested from the tag) attached.

Each image is built for amd64 and arm64, each on a runner of that architecture, and tagged as one
image: `docker pull` takes the robot's. The first time, make the packages public on GitHub
(Packages → package settings → visibility).

## 3. ROS packages (bloom)

For `apt install ros-jazzy-jarvis-*` from the ROS buildfarm. The first time, ask for a release
repository (`jarvis_ros2-release`, see the [bloom tutorial](https://docs.ros.org/en/jazzy/How-To-Guides/Releasing/Releasing-a-Package.html))
and add the repository to `rosdistro`; then for each release:

```bash
bloom-release --rosdistro jazzy --track jazzy jarvis_ros2     # --new-track the first time
bloom-release --rosdistro lyrical --track lyrical jarvis_ros2
```

bloom reads each `package.xml` and `CHANGELOG.rst`. The three Python services (`jarvis_lake`,
`jarvis_gateway`, `jarvis_agent`) pin dependencies that rosdep doesn't have (DuckDB, MCAP,
LiteLLM ...): their packages install, and need `pip install -r requirements.txt` next to them, or
use the Docker image. QUALITY_DECLARATION.md tracks it.

## 4. After

- Add a `Forthcoming` section back at the top of CHANGELOG.md (`## Forthcoming`) and of each
  `CHANGELOG.rst` (`Forthcoming` underlined with `-----------`).
- Check the images run: `docker run --rm ghcr.io/<owner>/jarvis:X.Y.Z ros2 pkg list | grep jarvis`,
  and that both architectures are there: `docker buildx imagetools inspect ghcr.io/<owner>/jarvis:X.Y.Z`.
