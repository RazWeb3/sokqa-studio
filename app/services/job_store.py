from app.schemas.sokqa import GeneratePackResponse


_jobs: dict[str, GeneratePackResponse] = {}


def save_job(response: GeneratePackResponse) -> None:
    _jobs[response.jobId] = response


def get_job(job_id: str) -> GeneratePackResponse | None:
    return _jobs.get(job_id)


def update_job(response: GeneratePackResponse) -> None:
    _jobs[response.jobId] = response
