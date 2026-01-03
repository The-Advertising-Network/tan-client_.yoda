from typing import Optional
import json


def set_mute_role(role_id: int) -> None:
    """Sets the mute role ID in the configuration file.
    Parameters:
        role_id (int): The ID of the mute role to set.
    """
    config_path = 'data/mute_role_config.json'
    config_data = {'mute_role_id': role_id}
    with open(config_path, 'w') as config_file:
        json.dump(config_data, config_file)

def get_mute_role() -> Optional[int]:
    """Retrieves the mute role ID from the configuration file.
    Returns:
        Optional[int]: The ID of the mute role, or None if not set.
    """
    config_path = 'data/mute_role_config.json'
    try:
        with open(config_path, 'r') as config_file:
            config_data = json.load(config_file)
            return config_data.get('mute_role_id')
    except (FileNotFoundError, json.JSONDecodeError):
        return None