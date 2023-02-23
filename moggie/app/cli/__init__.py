from .admin import CommandWelcome, CommandGrant, CommandContext
from .admin import CommandUnlock, CommandEnableEncryption, CommandImport
from .email import CommandEmail, CommandParse
from .notmuch import CommandSearch, CommandAddress, CommandShow, CommandCount
from .notmuch import CommandConfig, CommandTag, CommandReply
from .help import CommandHelp


CLI_COMMANDS = {
    CommandAddress.NAME: CommandAddress,
    CommandContext.NAME: CommandContext,
    CommandCount.NAME: CommandCount,
    CommandEmail.NAME: CommandEmail,
    CommandHelp.NAME: CommandHelp,
    CommandGrant.NAME: CommandGrant,
    CommandParse.NAME: CommandParse,
    CommandSearch.NAME: CommandSearch,
    CommandShow.NAME: CommandShow,
    CommandReply.NAME: CommandReply,
    CommandTag.NAME: CommandTag,
    CommandUnlock.NAME: CommandUnlock,
    CommandWelcome.NAME: CommandWelcome,

    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'config': CommandConfig}
