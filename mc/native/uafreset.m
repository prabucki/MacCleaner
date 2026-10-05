// uafreset — ask macOS's own asset service to delete Apple Intelligence model sets.
//
// Why not rm: each model's .AssetData is a read-only graft of a cryptex image, so
// even root gets EROFS (23 errors on 2026-10-05). The service that grafted it is the
// only thing that can tear it down. It does so on a "ResetAssetSets" operation.
//
// Private API, mapped by pared (github.com/4evy/pared, MIT) and used by RemoveMacAI
// (github.com/omlahore/RemoveMacAI, MIT). Every class and selector is checked before
// use, so a macOS update that renames them fails loudly instead of misbehaving.
//
//   uafreset status <asset set>...   JSON: {set: {"type": str|null, "bytes": int}}
//   uafreset reset  <asset set>...   JSON: {set: null on success, or an error string}
//
// Exit 0 when every set succeeded, 1 when any failed, 2 on bad usage.
// Built on demand by mc/modules/system.py; runs as the user, not root.

#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/runtime.h>

static NSString *const kFramework =
    @"/System/Library/PrivateFrameworks/UnifiedAssetFramework.framework/UnifiedAssetFramework";
static NSString *const kService = @"com.apple.siri.uaf.subscription.service";
static const int64_t kResetTimeout = 120;

@interface NSObject (UAFPrivate)
+ (id)defaultManager;
+ (NSXPCInterface *)defaultInterface;
+ (id)latestStatusForClients:(NSString *)name error:(NSError **)error;
- (id)getAssetSet:(NSString *)name;
- (NSString *)autoAssetType;
- (int64_t)downloadedFilesystemBytes;
- (oneway void)operationWithConfig:(NSDictionary *)configuration
                        completion:(void (^)(NSError *_Nullable))completion;
@end

static id assetType(NSString *set) {
  Class manager = NSClassFromString(@"UAFConfigurationManager");
  if (![manager respondsToSelector:@selector(defaultManager)]) return NSNull.null;
  id m = [manager defaultManager];
  if (![m respondsToSelector:@selector(getAssetSet:)]) return NSNull.null;
  id s = [m getAssetSet:set];
  if (![s respondsToSelector:@selector(autoAssetType)]) return NSNull.null;
  id type = [s autoAssetType];
  return [type isKindOfClass:NSString.class] ? type : NSNull.null;
}

static int64_t downloadedBytes(NSString *set) {
  Class manager = NSClassFromString(@"UAFAutoAssetManager");
  if (![manager respondsToSelector:@selector(latestStatusForClients:error:)]) return -1;
  id status = [manager latestStatusForClients:set error:NULL];
  if (![status respondsToSelector:@selector(downloadedFilesystemBytes)]) return -1;
  return [status downloadedFilesystemBytes];
}

static NSString *reset(NSString *set) {
  Class interfaceClass = NSClassFromString(@"UAFXPCProxyServiceInterface");
  if (![interfaceClass respondsToSelector:@selector(defaultInterface)])
    return @"asset service interface unavailable";

  NSXPCConnection *connection = [[NSXPCConnection alloc] initWithMachServiceName:kService options:0];
  connection.remoteObjectInterface = [interfaceClass defaultInterface];

  // A proxy answers every selector, so check the interface's protocol instead.
  SEL operation = @selector(operationWithConfig:completion:);
  Protocol *protocol = connection.remoteObjectInterface.protocol;
  if (protocol_getMethodDescription(protocol, operation, YES, YES).name == NULL &&
      protocol_getMethodDescription(protocol, operation, NO, YES).name == NULL)
    return @"asset service does not accept operations";

  [connection resume];
  dispatch_semaphore_t done = dispatch_semaphore_create(0);
  __block NSString *failure = nil;
  void (^finish)(NSError *) = ^(NSError *e) {
    @synchronized(connection) {
      if (e && !failure) failure = [NSString stringWithFormat:@"%@ %ld %@", e.domain, (long)e.code, e.userInfo];
    }
    dispatch_semaphore_signal(done);
  };
  id proxy = [connection remoteObjectProxyWithErrorHandler:finish];
  [proxy operationWithConfig:@{@"Operation" : @"ResetAssetSets", @"AssetSets" : @[ set ]} completion:finish];

  long timedOut = dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, kResetTimeout * NSEC_PER_SEC));
  [connection invalidate];
  if (timedOut) return [NSString stringWithFormat:@"no answer in %lld seconds", kResetTimeout];
  @synchronized(connection) {
    return failure;
  }
}

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc < 3 || (strcmp(argv[1], "status") != 0 && strcmp(argv[1], "reset") != 0)) {
      fprintf(stderr, "usage: uafreset status|reset <asset set>...\n");
      return 2;
    }
    if (dlopen(kFramework.fileSystemRepresentation, RTLD_NOW) == NULL) {
      fprintf(stderr, "cannot load UnifiedAssetFramework: %s\n", dlerror());
      return 1;
    }

    BOOL isReset = strcmp(argv[1], "reset") == 0;
    BOOL allOk = YES;
    NSMutableDictionary *out = [NSMutableDictionary dictionary];
    for (int i = 2; i < argc; i++) {
      NSString *set = @(argv[i]);
      if (isReset) {
        NSString *error = reset(set);
        if (error) allOk = NO;
        out[set] = error ?: NSNull.null;
      } else {
        out[set] = @{@"type" : assetType(set), @"bytes" : @(downloadedBytes(set))};
      }
    }

    NSData *json = [NSJSONSerialization dataWithJSONObject:out options:NSJSONWritingSortedKeys error:NULL];
    fwrite(json.bytes, 1, json.length, stdout);
    fputc('\n', stdout);
    return allOk ? 0 : 1;
  }
}
