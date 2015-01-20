#!/usr/bin/env python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: sdc
short_description: create or delete a virtualmachine or smartmachine in SDC
description:
     - Creates or deletes SDC instances. When created optionally waits for it to be 'running'. This module has a dependency on smartdc >= 0.2.0 which is available through pip
options:
  machine_id:
    description:
     - id of machine. Used only for deletion
  name:
    description:
      - Friendly name for this machine; default is a randomly generated name
    required: false
    default: null
  location:
    description:
      - hostname or fqdn for the SDC location you wish to use
    required: true
    default: us-east-1
  account:
    description:
      - sdc account name
    required: true
    default: null
  key_id:
    description:
      - The fingerprint of an SSH public key that has been added to the account set in account
    required: true
    default: null
  secret_key_path:
    description:
      - Path to the private key that corresponds to key_id
    required: false
    default: ~/.ssh/id_rsa
  image:
    description:
      - The image UUID to use for this machine
    required: true
    default: null
  package:
    description:
      - UUID of the package to use on provisioning
    required: false
  networks:
    description:
      - Comma separated list of desired networks ids, obtained from ListNetworks
    required: false
  count:
    description:
      - An integer value which indicates how many instances that should be created. Only used if count_tag or exact_count are missing (not recommended).
    required: false
    default: 1
    aliases: []
  exact_count:
    description:
      - An integer value which indicates how many instances that match the 'count_tag' parameter should be running.
    required: false
    default: null
    aliases: []
  count_tag:
    description:
      - Used with 'exact_count' to determine how many nodes based on a specific tag criteria should be running.  This can be expressed in multiple ways and is shown in the EXAMPLES section.  For instance, one can request 25 servers that are tagged with "class=webserver". Count tag(s) will always be added to created machines
    required: false
    default: null
    aliases: []
  tags:
    description:
      - Tags to assign to this instance.
    required: false
    default: null
    aliases: []
  wait:
    description:
      - wait for the instance to be in state 'running' before returning
    required: false
    default: "yes"
    choices: [ "yes", "no" ]
    aliases: []
  wait_timeout:
    description:
      - how long before wait gives up, in seconds
    default: 600
    aliases: []
  state:
    description:
      - create or delete instances
    required: false
    default: 'present'
    aliases: []

requirements: [ "smartdc" ]
author: Preston Marshall
'''

EXAMPLES = '''

# Provision single machine example
- local_action:
    module: sdc
    account: myaccount
    key_id: /myaccount/keys/mykey
    name: my-virtual-machine
    package: 486bb054-6a97-4ba3-97b7-2413d5f8e849
    image: 62f148f8-6e84-11e4-82c5-efca60348b9f
    location: 'us-east-1.api.joyentcloud.com'

# Ensure 5 machines with tag "webserver" are running, does not destroy existing machines
- local_action:
    module: sdc
    account: myaccount
    key_id: /myaccount/keys/mykey
    package: 486bb054-6a97-4ba3-97b7-2413d5f8e849
    image: 62f148f8-6e84-11e4-82c5-efca60348b9f
    location: 'us-east-1.api.joyentcloud.com'
    count_tag:
      role: webserver
    exact_count: 5

# Delete machines by their tags
- local_action:
    module: sdc
    account: myaccount
    key_id: /myaccount/keys/mykey
    location: 'us-east-1.api.joyentcloud.com'
    tags:
      role: webserver
    state: absent

# Delete machine by its id
- local_action:
    module: sdc
    account: myaccount
    key_id: /myaccount/keys/mykey
    machine_id: 51307f2f-02a3-4bdc-9b38-2f263eb43e2d
    location: 'us-east-1.api.joyentcloud.com'
    state: absent
'''

import os
import sys
import time
try:
    import smartdc
    from smartdc import DataCenter
    HAS_SMARTDC = True
except ImportError:
    HAS_SMARTDC = False

def _wait_for_status(module, machines, status):
    wait_timeout = int(module.params.get('wait_timeout'))
    wait_timeout_time = time.time() + wait_timeout
    while not all( sm.status() == status for sm in machines ) and wait_timeout_time > time.time():
        time.sleep(2)
    # Check again that all are in expected state. If not, we timed out
    if not all( sm.status() == status for sm in machines ):
        module.fail_json(msg="Timed out creating machines")

def list_existing_machines(module, sdc, tags):
    if not tags:
        module.fail_json(msg="Must have tags to list existing machines for deletion/counting")
    machines = sdc.machines(tags=tags, state='running')
    return machines


def create_virtual_machine(module, sdc):
    """
    Create new virtual machine

    module : AnsibleModule object
    sdc: authenticated sdc Datacenter object

    Returns:
        True if a new machine was created, false otherwise
    """
    name = module.params.get('name')
    package = module.params.get('package')
    image = module.params.get('image')
    networks = module.params.get('networks')
    wait = module.params.get('wait')
    wait_timeout = int(module.params.get('wait_timeout'))
    tags = module.params.get('tags')
    exact_count = int(module.params.get('exact_count'))
    count_tag = module.params.get('count_tag')
    count = int(module.params.get('count'))

    changed = False
    created_machines = []
    existing_machines = []
    full_tags = dict(tags.items() + count_tag.items()) #Force count_tag(s) to be a part of the instance tags to prevent user error

    # Get existing machines if we're using exact_count
    if count_tag and exact_count:
        existing_machines = list_existing_machines(module, sdc, count_tag)
        count_needed = exact_count - len(existing_machines)
    else:
        count_needed = count

    #Make sure we actually need to create machines, otherwise we can just give info about the existing ones
    if count_needed > 0:
        changed = True
        # Batch create, we'll wait later
        for x in range(count_needed):
            sm = sdc.create_machine(name=name, package=package, image=image,
                  networks=networks, tags=full_tags)
            created_machines.append(sm)
        # Wait on machines to be running if requested
        if wait:
            _wait_for_status(module, created_machines, 'running')
    all_machines = existing_machines + created_machines
    return changed, [sdc.raw_machine_data(sm.id) for sm in all_machines]



def delete_machines(module, sdc):
    """
    Deletes machines

    module : AnsibleModule object
    sdc: authenticated sdc Datacenter object

    Returns:
        True if machines were deleted, false otherwise
    """
    machines = []
    if module.params.get('tags'):
        machines = sdc.machines(tags=module.params.get('tags'))
    elif module.params.get('machine_id'):
        machines = [sdc.machine(module.params.get('machine_id'))]

    changed = False

    if machines:
        changed = True
        # Stop machines first, as the API docs say it is required, even though calling delete
        # works fine without stopping first. Stopping first also ensures that we avoid races with
        # the count_tag/exact_count functionality. Just deleting instances leaves them in state 'running'
        # for a bit which could cause subtle issues
        [sm.stop() for sm in machines]
        _wait_for_status(module, machines, 'stopped')
        [sm.delete() for sm in machines]

    return changed


def get_sdc_creds(module):
    # Check module args for credentials, then check environment vars
    # TODO: Figure out how to pull from local environment variables here
    account = module.params.get('account')
    if not account:
        module.fail_json(msg="No account provided")

    key_id = module.params.get('key_id')
    if not key_id:
        module.fail_json(msg="No key_id provided")

    return account, key_id


def main():
    module = AnsibleModule(
        argument_spec=dict(
            machine_id=dict(),
            name=dict(),
            location=dict(),
            account=dict(),
            key_id=dict(),
            image=dict(),
            package=dict(),
            tags=dict(type='dict',default={}),
            count=dict(type='int', default=1),
            count_tag=dict(type='dict',default={}),
            exact_count=dict(type='int'),
            networks=dict(type='list'),
            secret_key_path=dict(default='~/.ssh/id_rsa'),
            state=dict(default='present'),
            wait=dict(type='bool', default=True),
            wait_timeout=dict(default=600,type='int')
        )
    )
    if not HAS_SMARTDC:
        module.fail_json(msg='smartdc python module is required')
    # get credentials for creating a Datacenter object
    account, key_id = get_sdc_creds(module)
    # sdc top-level Datacenter object
    sdc = DataCenter(location=module.params.get('location'), key_id=key_id, login=account, secret=module.params.get('secret_key_path'))
    machines = []
    if module.params.get('state') == 'absent':
        changed = delete_machines(module, sdc)

    elif module.params.get('state') == 'present':
        # Changed is always set to true when provisioning new instances
        if not module.params.get('image'):
            module.fail_json(msg='image parameter is required for new instance')
        (changed, machines) = create_virtual_machine(module, sdc)

    module.exit_json(changed=changed, machines=json.loads(json.dumps(machines)))

# import module snippets
from ansible.module_utils.basic import *

main()