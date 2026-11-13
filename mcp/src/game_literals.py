"""External HS runtime contracts, not LS workbench branding.

Keep these exact names for the currently supported game adapter. This table is
not runtime discovery and does not add support for another game's identity.
"""

HS_GAME = {
    "executable": "hookshot",
    "renderer": "hookshot_vk.so",
    "log": "hookshot_log.txt",
    "build_profile": "hookshot_pieces.prof",
    "lift_jobs_env": "HOOKSHOT_LIFT_JOBS",
    "keep_build_files_env": "HOOKSHOT_KEEP_BUILD_FILES",
    "framelog_mime": "application/x-hookshot-framelog",
}
