// Minimal editor module: registers no UI, just hosts UBlendkitViewportLibrary.
#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FBlendkitViewportModule final : public IModuleInterface
{
public:
	virtual void StartupModule() override {}
	virtual void ShutdownModule() override {}
};
