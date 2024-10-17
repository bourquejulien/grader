#!/bin/env python3

'''
Minimum Python version required: 3.10

Dependencies:
- Docker SDK for Python
- Requests

Installation:
- pip install docker requests
'''

import sys

if sys.version_info.major < 3 or sys.version_info.minor < 10:
    info = \
        "You are currently running Python {}.{} which is antique.\n" \
        "To run this script you need a python version >= 3.10." \
        .format(sys.version_info.major, sys.version_info.minor)
    print(info)
    exit(1)


import signal
import time
import socket
import uuid
import subprocess


def imports() -> None:
    global requests, docker, DockerClient, Container, Network
    import requests
    import docker
    from docker import DockerClient
    from docker.models.containers import Container
    from docker.models.networks import Network


def install_dependencies(dependencies: list[str]) -> bool:
    print(f"Failed to load dependencies: {dependencies}")
    should_install = \
        input("Do you want to install them in the current environnement (Y, N)?\n") \
        .lower().startswith("y")
    if not should_install:
        return False

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *dependencies])
    except:
        print("Unable to install dependencies, did you install pip (https://pip.pypa.io/en/stable/installation/)?")
        return False

    return True


try:
    imports()
except:
    if not install_dependencies(["requests", "docker"]):
        print("Install the required dependencies to continue")
        exit(1)
    imports()


GRADING_IMAGE_NAME = "brqu/pre-cqi-prog-2025:latest"
GRADER_NAME_BASE = "grader"
TEAM_NAME_BASE = "team"
NETWORK_NAME_BASE = "grader_network"
RUN_COUNT = 10
GAME_TIMEOUT = 60
STOP_TIMEOUT = 5
TIMEOUT_SCORE = 89


def get_available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("localhost", 0))
        port = sock.getsockname()[1]
        return port


class StopToken:
    def __init__(self):
        self._is_canceled = False

    def is_canceled(self) -> None:
        return self._is_canceled

    def cancel(self, *kargs) -> None:
        self._is_canceled = True
        print("Stopping...")

    def wait(self, duration: int) -> bool:
        for _ in range(duration):
            time.sleep(1)
            if (self.is_canceled()):
                return False

        return True


class Grader:
    _client: DockerClient
    _team_image_name: str
    _grader_port: int
    _network_name: str
    _grading_container_name: str
    _team_container_name: str

    _network: Network | None
    _grading_container: Container | None
    _team_container: Container | None

    def __init__(self, client: DockerClient, team_image_name: str) -> None:
        run_id = uuid.uuid4().hex[:5]

        self._client = client
        self._team_image_name = team_image_name
        self._grader_port = get_available_port()
        self._grading_container = None
        self._team_container = None
        self._network = None
        self._network_name = f"{NETWORK_NAME_BASE}_{run_id}"
        self._grading_container_name = f"{GRADER_NAME_BASE}_{run_id}"
        self._team_container_name = f"{TEAM_NAME_BASE}_{run_id}"

    def prepare(self, token: StopToken) -> bool:
        print("Pulling containers...")
        self._client.images.pull(GRADING_IMAGE_NAME)

        if token.is_canceled():
            return False

        try:
            self._client.images.pull(self._team_image_name)
        except Exception as e:
            print(f"Unable to pull {self._team_image_name}: {e}")
            return False

        if token.is_canceled():
            return False

        self._cleanup()

        print("Starting grader...")
        self._network = self._client.networks.create(
            self._network_name, driver="bridge")
        self._grading_container = self._client.containers.run(GRADING_IMAGE_NAME, name=self._grading_container_name,
                                                              detach=True, network=self._network.name, hostname=GRADER_NAME_BASE, ports={"5000": self._grader_port})

        if not token.wait(STOP_TIMEOUT):
            return False

        response = requests.get(f"http://localhost:{self._grader_port}/status")
        if not response.ok and not response.content.startswith(b"No game available"):
            raise Exception("Failed to connect to grading server")

        print("Grader started successfully")
        return True

    def _cleanup(self):
        container: Container
        for container in self._client.containers.list(all=True):
            if container.name.startswith((GRADER_NAME_BASE, TEAM_NAME_BASE)):
                container.remove(force=True)

        for network in self._client.networks.list():
            if network.name.startswith(NETWORK_NAME_BASE):
                for container in network.containers:
                    network.disconnect(container, force=True)
                network.remove()

    def stop(self) -> None:
        self.reset()

        if self._grading_container is not None:
            self._grading_container.stop(timeout=STOP_TIMEOUT)

        if self._network is not None:
            self._network.remove()

    def grade(self, token: StopToken) -> int | None:
        try:
            self._team_container = self._client.containers.run(
                self._team_image_name, name=self._team_container_name, remove=True, detach=True, network=self._network.name, command=f"{GRADER_NAME_BASE}:5000")
        except Exception as e:
            print(f"Unable to launch bot container: {e}")
            return None

        for _ in range(GAME_TIMEOUT):
            if token.is_canceled():
                return None
            time.sleep(1)

            try:
                self._team_container.reload()
            except:
                break
            if self._team_container.status != "running":
                break

        try:
            response = requests.get(
                f"http://localhost:{self._grader_port}/status")
        except:
            print("Unable to access grader")
            return None

        if not response.ok:
            print("No game status available")
            return None

        game_response = response.json()
        if not game_response["game_over"]:
            print(f"Game exceeded {GAME_TIMEOUT}s")
            return None

        return game_response["score"]

    def reset(self) -> None:
        if self._team_container is None:
            return

        try:
            self._team_container.reload()
        except:
            self._team_container = None
            return

        if self._team_container.status == "running":
            print("Container not correctly stopped")

        self._team_container.remove(force=True)
        self._team_container = None


def grade(grader: Grader, token: StopToken) -> None:
    total_score = 0
    total_run = 0

    for i in range(RUN_COUNT):
        if token.is_canceled():
            return

        score = grader.grade(token)
        grader.reset()

        if score is None:
            score = TIMEOUT_SCORE
            print("RUN FAILED")

        total_score += score
        total_run += 1

        print(f"Score run {i+1}: {score}")

    average_score = total_score/max(total_run, 1)
    print(f"Average score after {total_run} runs: {average_score}")


def main() -> int:
    token = StopToken()
    client: DockerClient

    try:
        client = docker.from_env()
    except:
        noob_instructions = \
            "Unable to access Docker. Is docker installed and running (can you run \"docker run hello-world\")?\n" \
            "If you are on Linux and Docker is install, you probably need to allow Docker to be accessed from your user (https://docs.docker.com/engine/install/linux-postinstall/). You can also run this script as root, but thats NOT RECOMMENDED.\n" \
            "Else you need to install Docker: https://docs.docker.com/get-started/get-docker/.\n" \
            "Note: If you are using Windows, the WSL 2 Docker installation method is preferred."
        print(noob_instructions)
        return 1

    team_image_name: str
    if len(sys.argv) < 2:
        print("Input team image name:")
        team_image_name = input()
    else:
        team_image_name = sys.argv[1]

    signal.signal(signal.SIGINT, token.cancel)
    grader = Grader(client, team_image_name)

    if grader.prepare(token) and not token.is_canceled():
        grade(grader, token)

    grader.stop()

    return 1 if token.is_canceled() else 0


if __name__ == "__main__":
    exit(main())
