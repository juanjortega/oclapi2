# oclapi2
The new and improved OCL terminology service v2


#### Dev Setup
1. `sysctl -w vm.max_map_count=262144` #required by Elasticsearch
2. `docker-compose up -d`
3. Go to http://localhost:8000/swagger/ to benefit.

#### Run Checks
(use the `docker exec` command in a service started with `docker-compose up -d`)
1. Pylint (pep8):
   
   `docker exec -it oclapi2_api_1 pylint -j2 core` 

    or

   `docker-compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api pylint -j0 core`
2. Coverage

   `docker exec -it oclapi2_api_1 bash coverage.sh`

   or

   `docker-compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api bash coverage.sh`
3. Tests

    `docker exec -it oclapi2_api_1  python manage.py test --keepdb -v3` 

    or

    `docker-compose -f docker-compose.yml -f docker-compose.ci.yml run --rm api python manage.py test --keepdb -v3`

### Build image


#### Crear archivo .env

```conf
ENVIRONMENT=development|production
API_BASE_URL=http://localhost:8000
API_SUPERUSER_PASSWORD=qwertyu
API_SUPERUSER_TOKEN=1df23g4h5j67zz
```

#### Puesta en Marcha

```bash
docker-compose up -d
docker exec -it oclapi2_api_1 python manage.py search_index --rebuild -f --parallel
```

#### Limpiar Imagenes y base de datos

```bash
docker volume rm oclapi2_es-data
docker volume rm oclapi2_postgres-data
docker rmi openconceptlab/oclapi2:development
docker rmi openconceptlab/oclapi2:production
```

# errores

* redis error: 'vm.overcommit_memory = 1' to /etc/sysctl.conf and then reboot or run the command 'sysctl vm.overcommit_memory=1

### DB migrations
After modifying model you need to create migration files. Run:

`docker-compose run --rm api python manage.py makemigrations`

Make sure to commit newly created migration files.

### Release

Every build is a candidate for release.

In order to release please trigger the release build step in [our CI](https://ci.openmrs.org/browse/OCL-OCLAPI2/latest). Please note
that the maintenance version will be automatically increased after a successful release. It is desired only, if you are releasing the latest build and
should be turned off by setting the increaseMaintenanceRelease variable to false on the Run stage "Release" popup in other cases.

A deployment release will be automatically created and pushed to the staging environment.

### Deployment

In order to deploy please trigger the deployment [here](https://ci.openmrs.org/deploy/viewDeploymentProjectEnvironments.action?id=205619201).
Please use an existing deployment release.
