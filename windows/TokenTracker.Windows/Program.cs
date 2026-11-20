using System.Windows.Forms;
using System.Security.Principal;
using TokenTracker.Windows;

var userIdentity = WindowsIdentity.GetCurrent().User?.Value ?? Environment.UserName;
using var singleInstance = new Mutex(
    initiallyOwned: true,
    // Keep one instance across this user's login sessions without blocking a
    // different user signed in to the same PC.
    name: $@"Global\TokenTracker.Windows.SingleInstance.{userIdentity}",
    createdNew: out var createdNew);

if (!createdNew)
{
    return;
}

ApplicationConfiguration.Initialize();
Application.Run(new TrayAppContext());
