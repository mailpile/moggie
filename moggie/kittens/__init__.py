import asyncio


async def metadata_kitten(
        args=[],
        metadata_dir=None,
        encryption_keys=None,
        unique_app_id=None,
        auto_start=False):
    """
    Convenience method for launching, configuring and connecting to the
    moggie metadata kitten.
    """
    from .metadata import MetadataKitten

    kitten = MetadataKitten(args=args)
    await kitten.connect(auto_start=auto_start)

    if unique_app_id:
        await kitten.unique_app_id(set_id=unique_app_id)

    if encryption_keys and metadata_dir:
        await kitten.unlock(metadata_dir, encryption_keys)
    elif encryption_keys or metadata_dir:
        await kitten.quitquitquit()
        raise RuntimeError('Need both encryption keys and a metadata dir')

    return kitten


async def storage_kitten(
        args=[],
        unique_app_id=None,
        auto_start=False):
    """
    Convenience method for launching, configuring and connecting to the
    moggie storage kittens (triage and workers).
    """
    from .storage import StorageTriageKitten

    kitten = StorageTriageKitten(args=args)
    await kitten.connect(auto_start=auto_start)

    if unique_app_id:
        await kitten.unique_app_id(set_id=unique_app_id)

    return kitten
