# cyclebot

cyclebot polls live MLB data feeds to provide links to game highlights and alerts as players approach notable achievements such as [hitting for the cycle](https://en.wikipedia.org/wiki/Hitting_for_the_cycle).

## Quickstart

This project uses [Zappa](https://github.com/Miserlou/Zappa) to deploy a simple Python application to [AWS Lambda](https://aws.amazon.com/lambda/). If you haven't already, create a local [AWS credentials file](https://aws.amazon.com/blogs/security/a-new-and-standardized-way-to-manage-credentials-in-the-aws-sdks/).

Install requirements:

```sh
$ make requirements
```

Package and deploy the service:

```sh
$ make deploy
```

Finally, set environment variables the app needs to function. These include connection details for an external Redis instance. You can use a service like [ElastiCache](https://aws.amazon.com/elasticache/redis/) or [Redis Labs](https://redislabs.com/) for this.

## Development

cyclebot is a Python script. It can be run locally without using Lambda. First, start Redis using Docker Compose:

```sh
$ docker-compose up -d
```

Now you can run cyclebot:

```sh
$ make cyclebot
```

To run the linter ([Flake8](http://flake8.pycqa.org/)):

```sh
$ make lint
```

## Design

MLB operates an API that provides live data feeds of every game. cyclebot polls these streams. It looks for plays with a high "captivating index," a stat meant to indicate how interesting a given play is. It also monitors every player's batting and pitching performances. The bot makes a best effort to locate and share interesting highlight videos using a separate MLB content API. It also sends alerts as players approach notable achievements. These include cycles, no-hitters, and complete-game shutouts.
