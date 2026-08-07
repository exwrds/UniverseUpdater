# UniverseUpdater
This repository allows for you to programmatically update roblox games via an .rbxl (because manually doing it is really slow)

# Dependencies
This repository uses 1 external module which is httpx, you can install it via *pip install httpx*

# Getting an API Key for programmatic updating.
Go to: https://create.roblox.com/dashboard/credentials?activeTab=ApiKeysTab and Create an API Key,
The API Key needs the following Access Permissions: universe-places. Select what games you want to be able to be updated, then give
universe-places:write, Save & Generate the key and then paste the generated key into X-API-KEY within config.json

# Notes
the config comes broken btw you need to edit it for this to work,
you need to run this program with CMD in the folder that the .py is actually located in (because it uses cwd)
