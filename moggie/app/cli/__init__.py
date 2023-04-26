from .admin import CommandWelcome, CommandGrant, CommandContext
from .admin import CommandUnlock, CommandEnableEncryption, CommandImport
from .email import CommandEmail, CommandParse
from .notmuch import CommandSearch, CommandAddress, CommandShow, CommandCount
from .notmuch import CommandConfig, CommandTag, CommandReply
from .help import CommandHelp
from .openpgp import CommandPGPGetKeys, CommandPGPAddKeys, CommandPGPDelKeys
from .openpgp import CommandPGPSign, CommandPGPEncrypt, CommandPGPDecrypt

CLI_COMMANDS = {
    CommandHelp.NAME: CommandHelp,

    CommandAddress.NAME: CommandAddress,
    CommandContext.NAME: CommandContext,
    CommandCount.NAME: CommandCount,
    CommandEmail.NAME: CommandEmail,
    CommandGrant.NAME: CommandGrant,
    CommandParse.NAME: CommandParse,
    CommandSearch.NAME: CommandSearch,
    CommandShow.NAME: CommandShow,
    CommandReply.NAME: CommandReply,
    CommandTag.NAME: CommandTag,
    CommandUnlock.NAME: CommandUnlock,
    CommandWelcome.NAME: CommandWelcome,

    CommandPGPGetKeys.NAME: CommandPGPGetKeys,
    CommandPGPAddKeys.NAME: CommandPGPAddKeys,
    CommandPGPDelKeys.NAME: CommandPGPDelKeys,
    CommandPGPSign.NAME: CommandPGPSign,
    CommandPGPEncrypt.NAME: CommandPGPEncrypt,
    CommandPGPDecrypt.NAME: CommandPGPDecrypt,

    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'config': CommandConfig}
