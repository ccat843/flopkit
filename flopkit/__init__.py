# Copyright 2026 ccat843
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""flopkit — credit-safe FLOP agent participation toolkit.

Orchestrates the official @flop-labs CLI for all on-chain operations.
The toolkit itself never holds or touches the signing seed: every signed
on-chain action goes through the CLI + encrypted Polkadot keystore.
"""

__version__ = "0.1.0"
